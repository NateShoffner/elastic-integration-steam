"""Generate the Steam integration's Kibana dashboards.

The dashboards are written as by-value Lens panels in the saved object format
Kibana 8.19 exports (dashboard typeMigrationVersion 10.3.0), which is the
oldest Kibana the package supports, so they import cleanly on every supported
version. Re-run after changing any panel:

    python tools/dashboards/build_dashboards.py

and commit the regenerated files under packages/steam/kibana/dashboard/.
"""

from __future__ import annotations

import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parents[2] / "packages" / "steam" / "kibana" / "dashboard"

LOGS = "logs-*"
METRICS = "metrics-*"


# --------------------------------------------------------------------------
# Lens columns. Each builder returns {column_id: column}; formula builders
# return the visible column plus the hidden columns it references.
# --------------------------------------------------------------------------

def _kql(query):
    return {"language": "kuery", "query": query}


def count(cid, label, kql=None):
    col = {
        "customLabel": True,
        "dataType": "number",
        "isBucketed": False,
        "label": label,
        "operationType": "count",
        "params": {"emptyAsNull": False},
        "scale": "ratio",
        "sourceField": "___records___",
    }
    if kql:
        col["filter"] = _kql(kql)
    return {cid: col}


def metric(cid, op, field, label, kql=None, fmt=None, reduced=None, data_type="number"):
    col = {
        "customLabel": True,
        "dataType": data_type,
        "isBucketed": False,
        "label": label,
        "operationType": op,
        "params": {"emptyAsNull": True} if op in ("max", "min", "average", "sum", "unique_count") else {},
        "scale": "ratio" if data_type == "number" else "ordinal",
        "sourceField": field,
    }
    if op == "unique_count":
        col["params"] = {"emptyAsNull": False}
    if op == "last_value":
        col["params"] = {"showArrayValues": False, "sortField": "@timestamp"}
    if fmt:
        col["params"]["format"] = fmt
    if kql:
        col["filter"] = _kql(kql)
    if reduced:
        col["reducedTimeRange"] = reduced
    return {cid: col}


def per_hour(cid, op, field, label, kql=None):
    """A minutes field shown in hours: the formula `op(field) / 60`."""
    fn = {"max": "max", "last_value": "last_value", "sum": "sum"}[op]
    formula = f"{fn}({field}" + (f", kql='{kql}'" if kql else "") + ") / 60"
    inner = metric(f"{cid}X0", op, field, f"Part of {label}", kql=kql)
    inner[f"{cid}X0"]["customLabel"] = True
    math = {
        f"{cid}X1": {
            "customLabel": True,
            "dataType": "number",
            "isBucketed": False,
            "label": f"Part of {label}",
            "operationType": "math",
            "params": {"tinymathAst": {"args": [f"{cid}X0", 60], "location": {"min": 0, "max": len(formula)},
                                       "name": "divide", "text": formula, "type": "function"}},
            "references": [f"{cid}X0"],
            "scale": "ratio",
        }
    }
    main = {
        cid: {
            "customLabel": True,
            "dataType": "number",
            "isBucketed": False,
            "label": label,
            "operationType": "formula",
            "params": {"format": {"id": "number", "params": {"decimals": 1, "suffix": " h"}},
                       "formula": formula, "isFormulaBroken": False},
            "references": [f"{cid}X1"],
            "scale": "ratio",
        }
    }
    return {**main, **inner, **math}


def date_histogram(cid):
    return {
        cid: {
            "dataType": "date",
            "isBucketed": True,
            "label": "@timestamp",
            "operationType": "date_histogram",
            "params": {"dropPartials": False, "includeEmptyRows": True, "interval": "auto"},
            "scale": "interval",
            "sourceField": "@timestamp",
        }
    }


def terms(cid, field, label, size, order_by=None, secondary=None, other=False, missing=False):
    params = {
        "exclude": [],
        "excludeIsRegex": False,
        "include": [],
        "includeIsRegex": False,
        "missingBucket": missing,
        "orderBy": {"columnId": order_by, "type": "column"} if order_by else {"fallback": False, "type": "alphabetical"},
        "orderDirection": "desc" if order_by else "asc",
        "otherBucket": other,
        "parentFormat": {"id": "multi_terms" if secondary else "terms"},
        "size": size,
    }
    if secondary:
        params["secondaryFields"] = secondary
    return {
        cid: {
            "customLabel": True,
            "dataType": "string",
            "isBucketed": True,
            "label": label,
            "operationType": "terms",
            "params": params,
            "scale": "ordinal",
            "sourceField": field,
        }
    }


# --------------------------------------------------------------------------
# Lens visualizations.
# --------------------------------------------------------------------------

def _visible(columns):
    """Column order: buckets first, then visible metrics, then hidden parts."""
    bucketed = [c for c, v in columns.items() if v["isBucketed"]]
    hidden = [c for c, v in columns.items() if not v["isBucketed"] and v["label"].startswith("Part of ")]
    rest = [c for c in columns if c not in bucketed and c not in hidden]
    return bucketed + rest + hidden


def lens(title, description, index, query, columns, vis_type, visualization, layer="l"):
    return {
        "title": title,
        "description": description,
        "index": index,
        "attributes": {
            "description": description,
            "references": [{"id": index, "name": f"indexpattern-datasource-layer-{layer}", "type": "index-pattern"}],
            "state": {
                "adHocDataViews": {},
                "datasourceStates": {
                    "formBased": {
                        "layers": {
                            layer: {
                                "columnOrder": _visible(columns),
                                "columns": columns,
                                "incompleteColumns": {},
                                "sampling": 1,
                            }
                        }
                    }
                },
                "filters": [],
                "internalReferences": [],
                "query": _kql(query),
                "visualization": visualization,
            },
            "title": title,
            "type": "lens",
            "visualizationType": vis_type,
        },
    }


def metric_panel(title, description, index, query, columns, accessor, subtitle=None, breakdown=None, collapse=None):
    vis = {"layerId": "l", "layerType": "data", "metricAccessor": accessor}
    if subtitle:
        vis["subtitle"] = subtitle
    if breakdown:
        vis["breakdownByAccessor"] = breakdown
        vis["collapseFn"] = collapse
    return lens(title, description, index, query, columns, "lnsMetric", vis)


def xy_panel(title, description, index, query, columns, x, ys, series="bar_stacked", split=None,
             collapse=None, legend=True, y_title=None):
    if series == "line" and columns[x]["operationType"] == "date_histogram":
        # Sparse series: connect the points that exist rather than breaking the
        # line at every empty interval.
        columns[x]["params"]["includeEmptyRows"] = False
    layer = {
        "accessors": ys,
        "layerId": "l",
        "layerType": "data",
        "position": "top",
        "seriesType": series,
        "showGridlines": False,
        "xAccessor": x,
    }
    if split:
        layer["splitAccessor"] = split
    if collapse:
        layer["collapseFn"] = collapse
    vis = {
        "axisTitlesVisibilitySettings": {"x": False, "yLeft": bool(y_title), "yRight": False},
        "fittingFunction": "None",
        "gridlinesVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
        "labelsOrientation": {"x": 0, "yLeft": 0, "yRight": 0},
        "layers": [layer],
        # Split series are labelled by event.action and similar long values,
        # which a right-hand legend truncates; below the chart they fit.
        "legend": {"isVisible": legend, "position": "bottom" if split else "right"},
        "preferredSeriesType": series,
        "tickLabelsVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
        "valueLabels": "hide",
    }
    if y_title:
        vis["yTitle"] = y_title
    return lens(title, description, index, query, columns, "lnsXY", vis)


def table_panel(title, description, index, query, columns, sort=None):
    vis = {
        "columns": [{"columnId": c, "isTransposed": False} for c in _visible(columns)
                    if not columns[c]["label"].startswith("Part of ")],
        "layerId": "l",
        "layerType": "data",
    }
    if sort:
        vis["sorting"] = {"columnId": sort, "direction": "desc"}
    return lens(title, description, index, query, columns, "lnsDatatable", vis)


# --------------------------------------------------------------------------
# Dashboards.
# --------------------------------------------------------------------------

DASHBOARDS = [
    ("steam-game-activity", "Game activity"),
    ("steam-client-connectivity", "Client connectivity and sync"),
    ("steam-players-playtime", "Players and playtime"),
]


def ds(*names):
    return " or ".join(f'data_stream.dataset: "steam.{n}"' for n in names)


def control(cid, index, field, title, order):
    return cid, {
        "explicitInput": {
            "dataViewId": index,
            "enhancements": {},
            "exclude": False,
            "existsSelected": False,
            "fieldName": field,
            "hideExclude": False,
            "hideExists": False,
            "id": cid,
            "searchTechnique": "prefix",
            "selectedOptions": [],
            "singleSelect": False,
            "title": title,
        },
        "grow": True,
        "order": order,
        "type": "optionsListControl",
        "width": "medium",
    }


def build(dash_id, title, description, datasets, index, controls, rows):
    """rows: list of rows, each a list of (width, height, panel_dict)."""
    panels = []
    references = []

    links = []
    for order, (other_id, label) in enumerate(DASHBOARDS):
        links.append({
            "destinationRefName": f"link_{other_id}_dashboard",
            "id": other_id,
            "label": label,
            "options": {"openInNewTab": False, "useCurrentDateRange": True, "useCurrentFilters": False},
            "order": order,
            "type": "dashboardLink",
        })
        references.append({"id": other_id, "name": f"nav:link_{other_id}_dashboard", "type": "dashboard"})
    panels.append({
        "embeddableConfig": {"attributes": {"layout": "horizontal", "links": links}, "enhancements": {}},
        "gridData": {"h": 3, "i": "nav", "w": 48, "x": 0, "y": 0},
        "panelIndex": "nav",
        "title": "",
        "type": "links",
    })

    y = 3
    n = 0
    for row in rows:
        x = 0
        height = max(h for _, h, _ in row)
        for w, h, p in row:
            n += 1
            pid = f"p{n}"
            panels.append({
                "embeddableConfig": {"attributes": p["attributes"], "enhancements": {}},
                "gridData": {"h": h, "i": pid, "w": w, "x": x, "y": y},
                "panelIndex": pid,
                "title": p["title"],
                "type": "lens",
            })
            if p["description"]:
                panels[-1]["embeddableConfig"]["description"] = p["description"]
            for ref in p["attributes"]["references"]:
                references.append({"id": ref["id"], "name": f"{pid}:{ref['name']}", "type": ref["type"]})
            x += w
        assert x == 48, (title, x)
        y += height

    control_panels = {}
    for order, (cid, field, ctitle) in enumerate(controls):
        k, v = control(cid, index, field, ctitle, order)
        control_panels[k] = v
        references.append({"id": index, "name": f"controlGroup_{cid}:optionsListControlDataView", "type": "index-pattern"})

    dataset_filter = {
        "$state": {"store": "appState"},
        "meta": {
            "alias": None,
            "disabled": False,
            "index": "kibanaSavedObjectMeta.searchSourceJSON.filter[0].meta.index",
            "key": "data_stream.dataset",
            "negate": False,
            "params": [f"steam.{d}" for d in datasets],
            "type": "phrases",
        },
        "query": {"bool": {"minimum_should_match": 1,
                           "should": [{"match_phrase": {"data_stream.dataset": f"steam.{d}"}} for d in datasets]}},
    }
    references.append({"id": index, "name": "kibanaSavedObjectMeta.searchSourceJSON.filter[0].meta.index",
                       "type": "index-pattern"})

    return {
        "attributes": {
            "controlGroupInput": {
                "chainingSystem": "HIERARCHICAL",
                "controlStyle": "oneLine",
                "ignoreParentSettingsJSON": {"ignoreFilters": False, "ignoreQuery": False,
                                             "ignoreTimerange": False, "ignoreValidations": False},
                "panelsJSON": control_panels,
                "showApplySelections": False,
            },
            "description": description,
            "kibanaSavedObjectMeta": {"searchSourceJSON": {"filter": [dataset_filter], "query": _kql("")}},
            "optionsJSON": {"hidePanelTitles": False, "syncColors": False, "syncCursor": True,
                            "syncTooltips": False, "useMargins": True},
            "panelsJSON": panels,
            "timeRestore": False,
            "title": title,
            "version": 1,
        },
        "coreMigrationVersion": "8.8.0",
        "id": dash_id,
        "references": references,
        "type": "dashboard",
        "typeMigrationVersion": "10.3.0",
    }


# ---- [Logs Steam] Game activity ---------------------------------------------

def game_activity():
    gp = 'data_stream.dataset: "steam.gameprocess"'
    ct = 'data_stream.dataset: "steam.content"'
    start = 'event.action: "tracked-process-start"'
    end = 'event.action: "tracked-process-end"'
    abnormal = f'{end} and not process.exit_code: 0'
    rows = [
        [
            (12, 6, metric_panel(
                "Game processes started", "Processes the Steam client started tracking for a running game. A single "
                "launch often starts several, such as a launcher, the game and a crash or bug reporter, so this is "
                "not a count of play sessions.",
                LOGS, f"{gp} and {start}", count("m", ""), "m")),
            (12, 6, metric_panel(
                "Games played", "Distinct Steam applications that had at least one tracked game process started.",
                LOGS, f"{gp} and {start}", metric("m", "unique_count", "steam.app.id", ""), "m")),
            (12, 6, metric_panel(
                "Abnormal exits", "Tracked game processes that exited with a non-zero exit code. On Windows, "
                "-1073741819 (0xC0000005) is an access violation, which is how most crashes appear.",
                LOGS, f"{gp} and {abnormal}", count("m", ""), "m")),
            (12, 6, metric_panel(
                "Updates started", "Application updates the Steam client started downloading.",
                LOGS, f'{ct} and event.action: "app-update-started"', count("m", ""), "m")),
        ],
        [
            (32, 12, xy_panel(
                "Game processes started over time", "Tracked game processes started, by Steam application ID.",
                LOGS, f"{gp} and {start}",
                {**date_histogram("x"), **terms("s", "steam.app.id", "Application", 8, order_by="y"),
                 **count("y", "Processes")}, "x", ["y"], split="s")),
            (16, 12, xy_panel(
                "Most played applications", "Steam applications ranked by tracked game processes started.",
                LOGS, f"{gp} and {start}",
                {**terms("x", "steam.app.id", "Application", 10, order_by="y"), **count("y", "Processes")},
                "x", ["y"], series="bar_horizontal", legend=False)),
        ],
        [
            (24, 12, table_panel(
                "Game executables", "Executables the Steam client tracked as game processes, with the number of "
                "times each was started.",
                LOGS, gp,
                {**terms("r1", "process.name", "Executable", 20, order_by="m1"),
                 **terms("r2", "steam.app.id", "Application", 1, order_by="m1"),
                 **count("m1", "Started", kql=start),
                 **metric("m3", "max", "@timestamp", "Last seen", data_type="date")},
                sort="m1")),
            (24, 12, table_panel(
                "Abnormal exits", "Tracked game processes that exited with a non-zero code, by application and exit "
                "code. Exit lines record only the process ID, not the executable name.",
                LOGS, f"{gp} and {abnormal}",
                {**terms("r1", "steam.app.id", "Application", 20, order_by="m1"),
                 **terms("r2", "process.exit_code", "Exit code", 5, order_by="m1"),
                 **count("m1", "Exits"),
                 **metric("m2", "max", "@timestamp", "Last seen", data_type="date")},
                sort="m1")),
        ],
        [
            (24, 12, xy_panel(
                "Most updated applications", "Steam applications ranked by the number of updates the client "
                "started for them.",
                LOGS, f'{ct} and event.action: "app-update-started"',
                {**terms("x", "steam.app.id", "Application", 10, order_by="y"), **count("y", "Updates")},
                "x", ["y"], series="bar_horizontal", legend=False)),
            (24, 12, xy_panel(
                "Peak download rate", "Highest download rate the Steam client reported while downloading content, "
                "in megabits per second. Rates are only reported while a download is running.",
                LOGS, f'{ct} and event.action: "download-rate-reported"',
                {**date_histogram("x"),
                 **metric("y", "max", "steam.content.download.rate_mbps", "Peak",
                          fmt={"id": "number", "params": {"decimals": 1, "suffix": " Mbps"}})},
                "x", ["y"], series="bar", legend=False)),
        ],
        [
            (24, 12, xy_panel(
                "Content activity", "Content events by action: application state changes, depot updates, CDN "
                "connections and download interfaces. Noisy LAN peer-server discovery is excluded.",
                LOGS, f'{ct} and event.action: * and not event.action: ("peer-server-added" or "peer-server-removed")',
                {**date_histogram("x"), **terms("s", "event.action", "Action", 8, order_by="y"),
                 **count("y", "Events")}, "x", ["y"], split="s")),
            (24, 12, table_panel(
                "Content errors", "Failed content operations, such as CDN manifest requests, by depot and reason. "
                "CDN lines identify the depot being downloaded rather than the application.",
                LOGS, f'{ct} and event.outcome: "failure"',
                {**terms("r1", "event.action", "Action", 10, order_by="m1"),
                 **terms("r2", "steam.content.depot.id", "Depot", 10, order_by="m1", missing=True),
                 **terms("r3", "steam.content.error.reason", "Reason", 5, order_by="m1", missing=True),
                 **count("m1", "Failures"),
                 **metric("m2", "max", "@timestamp", "Last seen", data_type="date")},
                sort="m1")),
        ],
    ]
    return build(
        "steam-game-activity", "[Logs Steam] Game activity",
        "Games the Steam client ran on this machine and the content it downloaded for them, from the gameprocess "
        "and content logs. The client logs identify games by Steam application ID only; open [Metrics Steam] "
        "Players and playtime for game names.",
        ["gameprocess", "content"], LOGS,
        [("ctl-host", "host.name", "Host"), ("ctl-app", "steam.app.id", "Application")],
        rows)


# ---- [Logs Steam] Client connectivity and sync -----------------------------

def client_connectivity():
    cn = 'data_stream.dataset: "steam.connection"'
    cl = 'data_stream.dataset: "steam.cloud"'
    ws = 'data_stream.dataset: "steam.workshop"'
    ms = {"id": "number", "params": {"decimals": 0, "suffix": " ms"}}
    rows = [
        [
            (12, 6, metric_panel(
                "Connection manager latency", "Average round-trip time of the Steam client's pings to connection "
                "manager servers.",
                LOGS, f'{cn} and event.action: "cm-ping-completed"',
                metric("m", "average", "steam.connection.ping.latency_ms", "", fmt=ms), "m")),
            (12, 6, metric_panel(
                "Disconnects", "Times the client's connection to a Steam connection manager was closed.",
                LOGS, f'{cn} and event.action: "cm-connection-disconnected"', count("m", ""), "m")),
            (12, 6, metric_panel(
                "Cloud syncs completed", "Steam Cloud synchronisation passes that completed for an application.",
                LOGS, f'{cl} and event.action: "cloud-sync-completed"', count("m", ""), "m")),
            (12, 6, metric_panel(
                "Workshop items downloaded", "Workshop items the client finished downloading.",
                LOGS, f'{ws} and event.action: "workshop-item-downloaded"', count("m", ""), "m")),
        ],
        [
            (24, 12, xy_panel(
                "Connection manager latency over time", "Average and worst round-trip time of pings to Steam "
                "connection manager servers.",
                LOGS, f'{cn} and event.action: "cm-ping-completed"',
                {**date_histogram("x"),
                 **metric("y1", "average", "steam.connection.ping.latency_ms", "Average", fmt=ms),
                 **metric("y2", "max", "steam.connection.ping.latency_ms", "Worst", fmt=ms)},
                "x", ["y1", "y2"], series="line")),
            (24, 12, xy_panel(
                "Connection lifecycle", "Connection attempts, completed connections, disconnects, scheduled "
                "reconnects and failed pings to Steam connection managers.",
                LOGS, f'{cn} and event.action: ("cm-connection-started" or "cm-connection-completed" or '
                      '"cm-connection-disconnected" or "cm-auto-reconnect-scheduled" or "cm-ping-failed")',
                {**date_histogram("x"), **terms("s", "event.action", "Action", 5, order_by="y"),
                 **count("y", "Events")}, "x", ["y"], split="s")),
        ],
        [
            (16, 10, xy_panel(
                "Disconnect reasons", "Why connections to Steam connection managers were closed.",
                LOGS, f'{cn} and event.action: "cm-connection-disconnected"',
                {**terms("x", "steam.connection.disconnect.reason", "Reason", 8, order_by="y"),
                 **count("y", "Disconnects")}, "x", ["y"], series="bar_horizontal", legend=False)),
            (16, 10, xy_panel(
                "Ping failures", "Why pings to Steam connection managers failed.",
                LOGS, f'{cn} and event.action: "cm-ping-failed"',
                {**terms("x", "steam.connection.ping.failure_reason", "Reason", 8, order_by="y"),
                 **count("y", "Failures")}, "x", ["y"], series="bar_horizontal", legend=False)),
            (16, 10, xy_panel(
                "Connectivity test results", "Results of the client's internet connectivity tests.",
                LOGS, f'{cn} and event.action: "connectivity-test-completed"',
                {**terms("x", "steam.connection.connectivity_test.result", "Result", 8, order_by="y"),
                 **count("y", "Tests")}, "x", ["y"], series="bar_horizontal", legend=False)),
        ],
        [
            (24, 12, xy_panel(
                "Cloud sync activity", "Steam Cloud synchronisation passes started, completed, deferred and "
                "skipped as unnecessary.",
                LOGS, f'{cl} and event.action: ("cloud-sync-started" or "cloud-sync-completed" or '
                      '"cloud-sync-deferred" or "cloud-sync-not-needed" or "cloud-download-completed" or '
                      '"cloud-file-upload-started")',
                {**date_histogram("x"), **terms("s", "event.action", "Action", 6, order_by="y"),
                 **count("y", "Events")}, "x", ["y"], split="s")),
            (24, 12, xy_panel(
                "Cloud sync by application", "Steam Cloud synchronisation passes by Steam application ID.",
                LOGS, f'{cl} and event.action: ("cloud-sync-started" or "cloud-sync-deferred")',
                {**terms("x", "steam.app.id", "Application", 10, order_by="y"),
                 **terms("s", "event.action", "Action", 2, order_by="y"),
                 **count("y", "Syncs")}, "x", ["y"], series="bar_horizontal_stacked", split="s")),
        ],
        [
            (24, 12, xy_panel(
                "Workshop activity", "Workshop items subscribed, downloaded, removed and failing to update. "
                "Routine subscription list polling is excluded.",
                LOGS, f'{ws} and event.action: ("workshop-item-subscription-added" or "workshop-item-downloaded" '
                      'or "workshop-item-removed" or "workshop-item-details-lookup-failed" or '
                      '"workshop-item-details-update-failed" or "workshop-item-manifest-changed")',
                {**date_histogram("x"), **terms("s", "event.action", "Action", 6, order_by="y"),
                 **count("y", "Events")}, "x", ["y"], split="s")),
            (24, 12, table_panel(
                "Workshop failures", "Failed Workshop operations by application and result.",
                LOGS, f'{ws} and event.outcome: "failure"',
                {**terms("r1", "steam.app.id", "Application", 10, order_by="m1", missing=True),
                 **terms("r2", "event.action", "Action", 5, order_by="m1"),
                 **terms("r3", "steam.workshop.item.result", "Result", 5, order_by="m1", missing=True),
                 **count("m1", "Failures"),
                 **metric("m2", "max", "@timestamp", "Last seen", data_type="date")},
                sort="m1")),
        ],
    ]
    return build(
        "steam-client-connectivity", "[Logs Steam] Client connectivity and sync",
        "Health of the Steam client's connection to Steam, its Steam Cloud save synchronisation and its Workshop "
        "downloads, from the connection, cloud and workshop logs.",
        ["connection", "cloud", "workshop"], LOGS,
        [("ctl-host", "host.name", "Host"), ("ctl-app", "steam.app.id", "Application")],
        rows)


# ---- [Metrics Steam] Players and playtime -----------------------------------

def players_playtime():
    ps = 'data_stream.dataset: "steam.player_summary"'
    rp = 'data_stream.dataset: "steam.recently_played"'
    og = 'data_stream.dataset: "steam.owned_game"'
    lifetime_note = ("Lifetime playtime only ever grows, so the highest value seen in the time range is the most "
                     "recent one.")
    rows = [
        [
            (12, 6, metric_panel(
                "Accounts", "Steam accounts with a player summary in the selected time range.",
                METRICS, ps, metric("m", "unique_count", "steam.player.id", ""), "m")),
            (12, 6, metric_panel(
                "In game now", "Accounts reported in a game by their most recent player summaries, from the last "
                "15 minutes of the time range.",
                METRICS, f"{ps} and steam.player.in_game: true",
                metric("m", "unique_count", "steam.player.id", "", reduced="15m"), "m")),
            (12, 6, metric_panel(
                "Lifetime playtime", f"Total playtime across every owned game of every account. {lifetime_note}",
                METRICS, og,
                {**per_hour("m", "max", "steam.playtime.forever.minutes", "All accounts"),
                 **terms("b", "steam.player.id", "Game", 10000, secondary=["steam.app.id"])},
                "m", breakdown="b", collapse="sum")),
            (12, 6, metric_panel(
                "Games owned", "Distinct games across the libraries of every account. Free games only count once "
                "they have been played.",
                METRICS, og, metric("m", "unique_count", "steam.app.id", ""), "m")),
        ],
        [
            (24, 12, table_panel(
                "Account status", "Each account's most recent player summary: online status, the game it is in, and "
                "when it was last online. Private profiles report Offline whatever their real status.",
                METRICS, ps,
                {**terms("r1", "steam.player.persona_name", "Persona", 50, order_by="m4"),
                 **metric("m1", "last_value", "steam.player.persona_state.name", "Status", data_type="string"),
                 **metric("m2", "last_value", "steam.app.name", "In game", data_type="string"),
                 **metric("m3", "last_value", "steam.player.visibility.name", "Profile", data_type="string"),
                 **metric("m5", "last_value", "steam.player.last_logoff", "Last logoff", data_type="date"),
                 **metric("m4", "max", "@timestamp", "Last polled", data_type="date")},
                sort="m4")),
            (24, 12, xy_panel(
                "Accounts in game", "Accounts reported in a game, over time.",
                METRICS, f"{ps} and steam.player.in_game: true",
                {**date_histogram("x"), **terms("s", "steam.app.name", "Game", 8, order_by="y"),
                 **metric("y", "unique_count", "steam.player.id", "Accounts")},
                "x", ["y"], split="s")),
        ],
        [
            (24, 14, xy_panel(
                "Two-week playtime by game", "Playtime in Steam's rolling two-week window, from each account's "
                "most recent report in the time range, stacked by account. A game that has left the window keeps "
                "its last reported value until it falls out of the time range.",
                METRICS, rp,
                {**terms("x", "steam.app.name", "Game", 15, order_by="yX0"),
                 **terms("s", "steam.player.id", "Account", 20, order_by="yX0"),
                 **per_hour("y", "last_value", "steam.playtime.two_weeks.minutes", "Two-week playtime")},
                "x", ["y"], series="bar_horizontal_stacked", split="s")),
            (24, 14, xy_panel(
                "Most played games", f"Lifetime playtime per game, stacked by account. {lifetime_note}",
                METRICS, og,
                {**terms("x", "steam.app.name", "Game", 15, order_by="yX0"),
                 **terms("s", "steam.player.id", "Account", 20, order_by="yX0"),
                 **per_hour("y", "max", "steam.playtime.forever.minutes", "Lifetime playtime")},
                "x", ["y"], series="bar_horizontal_stacked", split="s")),
        ],
        [
            (24, 12, xy_panel(
                "Lifetime playtime by platform", "Lifetime playtime per account, split by the platform it was "
                "played on. The Steam Web API only reports platforms for the account that owns the API key, so "
                "other accounts are not listed. Steam began tracking platforms later than total playtime, so the "
                f"platforms do not add up to the lifetime total. {lifetime_note}",
                METRICS, f"{og} and steam.playtime.windows.minutes: *",
                {**terms("x", "steam.player.id", "Account", 20, order_by="y1X0"),
                 **terms("b", "steam.app.id", "Game", 10000),
                 **per_hour("y1", "max", "steam.playtime.windows.minutes", "Windows"),
                 **per_hour("y2", "max", "steam.playtime.linux.minutes", "Linux"),
                 **per_hour("y3", "max", "steam.playtime.mac.minutes", "macOS"),
                 **per_hour("y4", "max", "steam.playtime.deck.minutes", "Steam Deck")},
                "x", ["y1", "y2", "y3", "y4"], series="bar_horizontal_stacked", split="b", collapse="sum")),
            (24, 12, table_panel(
                "Recently launched games", "Games by when they were last launched. The Steam Web API only reports "
                "this for the account that owns the API key, and games that have never been launched are not "
                "listed.",
                METRICS, f"{og} and steam.playtime.last_played: *",
                {**terms("r1", "steam.app.name", "Game", 25, order_by="m1"),
                 **terms("r2", "steam.player.id", "Account", 5, order_by="m1"),
                 **metric("m1", "max", "steam.playtime.last_played", "Last played", data_type="date"),
                 **per_hour("m2", "max", "steam.playtime.forever.minutes", "Lifetime playtime")},
                sort="m1")),
        ],
    ]
    return build(
        "steam-players-playtime", "[Metrics Steam] Players and playtime",
        "Presence, recent playtime and lifetime playtime of the Steam accounts polled through the Steam Web API, "
        "from the player_summary, recently_played and owned_game data streams.",
        ["player_summary", "recently_played", "owned_game"], METRICS,
        [("ctl-player", "steam.player.id", "Account")],
        rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for dash in (game_activity(), client_connectivity(), players_playtime()):
        path = OUT / f"{dash['id']}.json"
        path.write_text(json.dumps(dash, indent=4, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
