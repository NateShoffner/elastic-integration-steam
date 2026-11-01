"""Generate the Steam integration's Kibana dashboards.

The dashboards are written as by-value Lens panels in the saved object format
Kibana 8.19 exports (dashboard typeMigrationVersion 10.3.0), which is the
oldest Kibana the package supports, so they import cleanly on every supported
version. Re-run after changing any panel:

    python tools/dashboards/build_dashboards.py

and commit the regenerated files under packages/steam/kibana/dashboard/.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parents[2] / "packages" / "steam" / "kibana" / "dashboard"

LOGS = "logs-*"
METRICS = "metrics-*"

# Lookup index the package's app_name transform maintains from the Web API data
# streams: one document per steam.app.id with its latest steam.app.name. The
# client logs only record application IDs, so panels that show applications
# are ES|QL queries that join against it. Applications no polled account has
# reported, such as the Steam client itself, keep their ID.
APP_LOOKUP = "steam_lookup.app_name"
WITH_APP_NAME = (f"| LOOKUP JOIN {APP_LOOKUP} ON steam.app.id\n"
                 "| EVAL application = COALESCE(steam.app.name, steam.app.id)\n")


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
             collapse=None, legend=True, y_title=None, legend_position=None):
    if series == "line" and columns[x]["operationType"] == "date_histogram":
        # Sparse series: connect the points that exist rather than breaking the
        # line at every empty interval.
        columns[x]["params"]["includeEmptyRows"] = False
    vis = _xy_vis(x, ys, series, split, collapse, legend, y_title, legend_position)
    return lens(title, description, index, query, columns, "lnsXY", vis)


def _xy_vis(x, ys, series, split, collapse, legend, y_title, legend_position):
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
        "legend": {"isVisible": legend,
                   "position": legend_position or ("bottom" if split else "right")},
        "preferredSeriesType": series,
        "tickLabelsVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
        "valueLabels": "hide",
    }
    if y_title:
        vis["yTitle"] = y_title
    return vis


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
# ES|QL (text-based) Lens panels, used where a panel needs the application
# names from APP_LOOKUP. Columns are (name, label, type) with type one of
# number, string or date; name is the ES|QL output column. Dashboard filters,
# controls and the time range still apply to the FROM index.
# --------------------------------------------------------------------------

_ES_TYPES = {"number": "long", "string": "keyword", "date": "date"}


def esql(title, description, index, query, columns, vis_type, visualization, layer="l"):
    dv = hashlib.sha256(index.encode()).hexdigest()
    cols = []
    for name, label, typ in columns:
        col = {"columnId": name, "customLabel": True, "fieldName": name, "label": label,
               "meta": {"esType": _ES_TYPES[typ], "type": typ}}
        if typ == "number":
            col["inMetricDimension"] = True
        if typ == "date":
            # Without a format, ES|QL dates render as raw ISO 8601 strings.
            col["params"] = {"format": {"id": "date"}}
        cols.append(col)
    return {
        "title": title,
        "description": description,
        "esql": query,
        "attributes": {
            "description": description,
            "references": [],
            "state": {
                "adHocDataViews": {dv: {
                    "allowHidden": False, "allowNoIndex": False, "fieldFormats": {}, "id": dv, "name": index,
                    "runtimeFieldMap": {}, "sourceFilters": [], "timeFieldName": "@timestamp", "title": index,
                    "type": "esql"}},
                "datasourceStates": {"textBased": {
                    "indexPatternRefs": [{"id": dv, "timeField": "@timestamp", "title": index}],
                    "layers": {layer: {"columns": cols, "index": dv, "query": {"esql": query},
                                       "timeField": "@timestamp"}},
                }},
                "filters": [],
                "needsRefresh": False,
                "query": {"esql": query},
                "visualization": visualization,
            },
            "title": title,
            "type": "lens",
            "visualizationType": vis_type,
        },
    }


def esql_xy(title, description, index, query, columns, x, ys, series="bar_stacked", split=None,
            legend=True, legend_position=None):
    vis = _xy_vis(x, ys, series, split, None, legend, None, legend_position)
    return esql(title, description, index, query, columns, "lnsXY", vis)


def esql_table(title, description, index, query, columns, sort=None):
    vis = {"columns": [{"columnId": c[0], "isTransposed": False} for c in columns],
           "layerId": "l", "layerType": "data"}
    if sort:
        vis["sorting"] = {"columnId": sort, "direction": "desc"}
    return esql(title, description, index, query, columns, "lnsDatatable", vis)


# --------------------------------------------------------------------------
# Dashboards.
# --------------------------------------------------------------------------

DASHBOARDS = [
    ("steam-game-activity", "Game activity"),
    ("steam-client-connectivity", "Client connectivity and sync"),
    ("steam-players-playtime", "Players and playtime"),
]


GP = "logs-steam.gameprocess-*"
CT = "logs-steam.content-*"
CL = "logs-steam.cloud-*"
WS = "logs-steam.workshop-*"
TS = "BUCKET(@timestamp, 50, ?_tstart, ?_tend)"
NAMES = ("Game names come from the Steam Web API data streams; games they have not reported show their "
         "application ID.")


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
            if "esql" in p:
                panels[-1]["embeddableConfig"].update({"filters": [], "query": {"esql": p["esql"]}})
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
                "Game processes started", "Processes Steam tracked for running games. One launch can start several, such as a launcher and a crash reporter, so this is not a count of sessions.",
                LOGS, f"{gp} and {start}", count("m", ""), "m")),
            (12, 6, metric_panel(
                "Games played", "Games with at least one process started.",
                LOGS, f"{gp} and {start}", metric("m", "unique_count", "steam.app.id", ""), "m")),
            (12, 6, metric_panel(
                "Abnormal exits", "Game processes that exited with a non-zero code. -1073741819 is a Windows access violation, the usual crash.",
                LOGS, f"{gp} and {abnormal}", count("m", ""), "m")),
            (12, 6, metric_panel(
                "Updates started", "Game updates Steam started downloading.",
                LOGS, f'{ct} and event.action: "app-update-started"', count("m", ""), "m")),
        ],
        [
            (32, 12, esql_xy(
                "Game processes started over time", "Game processes started, by game.",
                GP, f"FROM {GP}\n| WHERE event.action == \"tracked-process-start\"\n{WITH_APP_NAME}"
                    f"| STATS processes = COUNT(*) BY timestamp = {TS}, application",
                [("timestamp", "@timestamp", "date"), ("application", "Application", "string"),
                 ("processes", "Processes", "number")],
                "timestamp", ["processes"], split="application")),
            (16, 12, esql_xy(
                "Most played applications", "Games ranked by processes started.",
                GP, f"FROM {GP}\n| WHERE event.action == \"tracked-process-start\"\n{WITH_APP_NAME}"
                    "| STATS processes = COUNT(*) BY application\n| SORT processes DESC\n| LIMIT 10",
                [("application", "Application", "string"), ("processes", "Processes", "number")],
                "application", ["processes"], series="bar_horizontal", legend=False)),
        ],
        [
            (24, 12, esql_table(
                "Game executables", "Executables Steam tracked for each game, and how often each started.",
                GP, f"FROM {GP}\n| WHERE event.action == \"tracked-process-start\"\n{WITH_APP_NAME}"
                    "| STATS started = COUNT(*), last_seen = MAX(@timestamp) BY executable = process.name, "
                    "application\n| SORT started DESC\n| LIMIT 20",
                [("executable", "Executable", "string"), ("application", "Application", "string"),
                 ("started", "Started", "number"), ("last_seen", "Last seen", "date")],
                sort="started")),
            (24, 12, esql_table(
                "Abnormal exits", "Non-zero exits by game and exit code. Exit lines do not record the executable.",
                GP, f"FROM {GP}\n| WHERE event.action == \"tracked-process-end\" AND process.exit_code != 0\n"
                    f"{WITH_APP_NAME}"
                    "| STATS exits = COUNT(*), last_seen = MAX(@timestamp) BY application, "
                    "exit_code = TO_STRING(process.exit_code)\n| SORT exits DESC\n| LIMIT 20",
                [("application", "Application", "string"), ("exit_code", "Exit code", "string"),
                 ("exits", "Exits", "number"), ("last_seen", "Last seen", "date")],
                sort="exits")),
        ],
        [
            (24, 12, esql_xy(
                "Most updated applications", "Games ranked by updates started.",
                CT, f"FROM {CT}\n| WHERE event.action == \"app-update-started\"\n{WITH_APP_NAME}"
                    "| STATS updates = COUNT(*) BY application\n| SORT updates DESC\n| LIMIT 10",
                [("application", "Application", "string"), ("updates", "Updates", "number")],
                "application", ["updates"], series="bar_horizontal", legend=False)),
            (24, 12, xy_panel(
                "Peak download rate", "Highest download rate reported while downloading.",
                LOGS, f'{ct} and event.action: "download-rate-reported"',
                {**date_histogram("x"),
                 **metric("y", "max", "steam.content.download.rate_mbps", "Peak",
                          fmt={"id": "number", "params": {"decimals": 1, "suffix": " Mbps"}})},
                "x", ["y"], series="bar", legend=False)),
        ],
        [
            (24, 12, xy_panel(
                "Content activity", "Content events by action, excluding LAN peer discovery.",
                LOGS, f'{ct} and event.action: * and not event.action: ("peer-server-added" or "peer-server-removed")',
                {**date_histogram("x"), **terms("s", "event.action", "Action", 8, order_by="y"),
                 **count("y", "Events")}, "x", ["y"], split="s")),
            (24, 12, table_panel(
                "Content errors", "Failed content operations by depot and reason.",
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
        "Games launched on this machine and the content Steam downloaded for them. "
        f"{NAMES}",
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
                "Connection manager latency", "Average ping to Steam connection managers.",
                LOGS, f'{cn} and event.action: "cm-ping-completed"',
                metric("m", "average", "steam.connection.ping.latency_ms", "", fmt=ms), "m")),
            (12, 6, metric_panel(
                "Disconnects", "Connections to Steam that were closed.",
                LOGS, f'{cn} and event.action: "cm-connection-disconnected"', count("m", ""), "m")),
            (12, 6, metric_panel(
                "Cloud syncs completed", "Steam Cloud sync passes that completed.",
                LOGS, f'{cl} and event.action: "cloud-sync-completed"', count("m", ""), "m")),
            (12, 6, metric_panel(
                "Workshop items downloaded", "Workshop items downloaded.",
                LOGS, f'{ws} and event.action: "workshop-item-downloaded"', count("m", ""), "m")),
        ],
        [
            (24, 12, xy_panel(
                "Connection manager latency over time", "Average and worst ping to Steam connection managers.",
                LOGS, f'{cn} and event.action: "cm-ping-completed"',
                {**date_histogram("x"),
                 **metric("y1", "average", "steam.connection.ping.latency_ms", "Average", fmt=ms),
                 **metric("y2", "max", "steam.connection.ping.latency_ms", "Worst", fmt=ms)},
                "x", ["y1", "y2"], series="line")),
            (24, 12, xy_panel(
                "Connection lifecycle", "Connects, disconnects, reconnects and failed pings.",
                LOGS, f'{cn} and event.action: ("cm-connection-started" or "cm-connection-completed" or '
                      '"cm-connection-disconnected" or "cm-auto-reconnect-scheduled" or "cm-ping-failed")',
                {**date_histogram("x"), **terms("s", "event.action", "Action", 5, order_by="y"),
                 **count("y", "Events")}, "x", ["y"], split="s")),
        ],
        [
            (16, 10, xy_panel(
                "Disconnect reasons", "Why connections to Steam closed.",
                LOGS, f'{cn} and event.action: "cm-connection-disconnected"',
                {**terms("x", "steam.connection.disconnect.reason", "Reason", 8, order_by="y"),
                 **count("y", "Disconnects")}, "x", ["y"], series="bar_horizontal", legend=False)),
            (16, 10, xy_panel(
                "Ping failures", "Why pings to connection managers failed.",
                LOGS, f'{cn} and event.action: "cm-ping-failed"',
                {**terms("x", "steam.connection.ping.failure_reason", "Reason", 8, order_by="y"),
                 **count("y", "Failures")}, "x", ["y"], series="bar_horizontal", legend=False)),
            (16, 10, xy_panel(
                "Connectivity test results", "Results of Steam's internet connectivity tests.",
                LOGS, f'{cn} and event.action: "connectivity-test-completed"',
                {**terms("x", "steam.connection.connectivity_test.result", "Result", 8, order_by="y"),
                 **count("y", "Tests")}, "x", ["y"], series="bar_horizontal", legend=False)),
        ],
        [
            (24, 12, xy_panel(
                "Cloud sync activity", "Steam Cloud sync passes by outcome.",
                LOGS, f'{cl} and event.action: ("cloud-sync-started" or "cloud-sync-completed" or '
                      '"cloud-sync-deferred" or "cloud-sync-not-needed" or "cloud-download-completed" or '
                      '"cloud-file-upload-started")',
                {**date_histogram("x"), **terms("s", "event.action", "Action", 6, order_by="y"),
                 **count("y", "Events")}, "x", ["y"], split="s")),
            (24, 12, esql_xy(
                "Cloud sync by application", "Sync passes started and deferred, by game.",
                CL, f"FROM {CL}\n| WHERE event.action IN (\"cloud-sync-started\", \"cloud-sync-deferred\")\n"
                    f"{WITH_APP_NAME}"
                    # Capitalised because the legend shows ES|QL column names.
                    "| STATS Started = COUNT(*) WHERE event.action == \"cloud-sync-started\", "
                    "Deferred = COUNT(*) WHERE event.action == \"cloud-sync-deferred\" BY application\n"
                    "| SORT Started + Deferred DESC\n| LIMIT 10",
                [("application", "Application", "string"), ("Started", "Started", "number"),
                 ("Deferred", "Deferred", "number")],
                "application", ["Started", "Deferred"], series="bar_horizontal_stacked",
                legend_position="right")),
        ],
        [
            (24, 12, xy_panel(
                "Workshop activity", "Workshop items subscribed, downloaded, removed or failing to update.",
                LOGS, f'{ws} and event.action: ("workshop-item-subscription-added" or "workshop-item-downloaded" '
                      'or "workshop-item-removed" or "workshop-item-details-lookup-failed" or '
                      '"workshop-item-details-update-failed" or "workshop-item-manifest-changed")',
                {**date_histogram("x"), **terms("s", "event.action", "Action", 6, order_by="y"),
                 **count("y", "Events")}, "x", ["y"], split="s")),
            (24, 12, esql_table(
                "Workshop failures", "Failed Workshop operations by game and result.",
                WS, f"FROM {WS}\n| WHERE event.outcome == \"failure\"\n{WITH_APP_NAME}"
                    "| STATS failures = COUNT(*), last_seen = MAX(@timestamp) BY application, "
                    "action = event.action, result = steam.workshop.item.result\n| SORT failures DESC\n| LIMIT 20",
                [("application", "Application", "string"), ("action", "Action", "string"),
                 ("result", "Result", "string"), ("failures", "Failures", "number"),
                 ("last_seen", "Last seen", "date")],
                sort="failures")),
        ],
    ]
    return build(
        "steam-client-connectivity", "[Logs Steam] Client connectivity and sync",
        "Steam connection health, Steam Cloud save sync and Workshop downloads. "
        f"{NAMES}",
        ["connection", "cloud", "workshop"], LOGS,
        [("ctl-host", "host.name", "Host"), ("ctl-app", "steam.app.id", "Application")],
        rows)


# ---- [Metrics Steam] Players and playtime -----------------------------------

def players_playtime():
    ps = 'data_stream.dataset: "steam.player_summary"'
    rp = 'data_stream.dataset: "steam.recently_played"'
    og = 'data_stream.dataset: "steam.owned_game"'
    rows = [
        [
            (12, 6, metric_panel(
                "Accounts", "Accounts polled in the time range.",
                METRICS, ps, metric("m", "unique_count", "steam.player.id", ""), "m")),
            (12, 6, metric_panel(
                "In game now", "Accounts in a game during the last 15 minutes of the time range.",
                METRICS, f"{ps} and steam.player.in_game: true",
                metric("m", "unique_count", "steam.player.id", "", reduced="15m"), "m")),
            (12, 6, metric_panel(
                "Lifetime playtime", "Total playtime across all accounts and games.",
                METRICS, og,
                {**per_hour("m", "max", "steam.playtime.forever.minutes", "All accounts"),
                 **terms("b", "steam.player.id", "Game", 10000, secondary=["steam.app.id"])},
                "m", breakdown="b", collapse="sum")),
            (12, 6, metric_panel(
                "Games owned", "Distinct games across all libraries. Free games count once played.",
                METRICS, og, metric("m", "unique_count", "steam.app.id", ""), "m")),
        ],
        [
            (24, 12, table_panel(
                "Account status", "Each account's latest status. Private profiles always show Offline.",
                METRICS, ps,
                {**terms("r1", "steam.player.persona_name", "Persona", 50, order_by="m4"),
                 **metric("m1", "last_value", "steam.player.persona_state.name", "Status", data_type="string"),
                 **metric("m2", "last_value", "steam.app.name", "In game", data_type="string"),
                 **metric("m3", "last_value", "steam.player.visibility.name", "Profile", data_type="string"),
                 **metric("m5", "last_value", "steam.player.last_logoff", "Last logoff", data_type="date"),
                 **metric("m4", "max", "@timestamp", "Last polled", data_type="date")},
                sort="m4")),
            (24, 12, xy_panel(
                "Accounts in game", "Accounts in a game over time, by game.",
                METRICS, f"{ps} and steam.player.in_game: true",
                {**date_histogram("x"), **terms("s", "steam.app.name", "Game", 8, order_by="y"),
                 **metric("y", "unique_count", "steam.player.id", "Accounts")},
                "x", ["y"], split="s")),
        ],
        [
            (24, 14, xy_panel(
                "Two-week playtime by game", "Playtime in Steam's rolling two-week window, by game and account.",
                METRICS, rp,
                {**terms("x", "steam.app.name", "Game", 15, order_by="yX0"),
                 **terms("s", "steam.player.id", "Account", 20, order_by="yX0"),
                 **per_hour("y", "last_value", "steam.playtime.two_weeks.minutes", "Two-week playtime")},
                "x", ["y"], series="bar_horizontal_stacked", split="s")),
            (24, 14, xy_panel(
                "Most played games", "Lifetime playtime by game and account.",
                METRICS, og,
                {**terms("x", "steam.app.name", "Game", 15, order_by="yX0"),
                 **terms("s", "steam.player.id", "Account", 20, order_by="yX0"),
                 **per_hour("y", "max", "steam.playtime.forever.minutes", "Lifetime playtime")},
                "x", ["y"], series="bar_horizontal_stacked", split="s")),
        ],
        [
            (24, 12, xy_panel(
                "Lifetime playtime by platform", "Lifetime playtime by platform. Only reported for the API key's owner, and does not add up to the lifetime total.",
                METRICS, f"{og} and steam.playtime.windows.minutes: *",
                {**terms("x", "steam.player.id", "Account", 20, order_by="y1X0"),
                 **terms("b", "steam.app.id", "Game", 10000),
                 **per_hour("y1", "max", "steam.playtime.windows.minutes", "Windows"),
                 **per_hour("y2", "max", "steam.playtime.linux.minutes", "Linux"),
                 **per_hour("y3", "max", "steam.playtime.mac.minutes", "macOS"),
                 **per_hour("y4", "max", "steam.playtime.deck.minutes", "Steam Deck")},
                "x", ["y1", "y2", "y3", "y4"], series="bar_horizontal_stacked", split="b", collapse="sum",
                # Four short platform names fit beside the chart; below it the
                # fourth wraps onto a row the panel height clips.
                legend_position="right")),
            (24, 12, table_panel(
                "Recently launched games", "Games by when they were last launched. Only reported for the API key's owner.",
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
        "Presence and playtime of the accounts polled through the Steam Web API.",
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
