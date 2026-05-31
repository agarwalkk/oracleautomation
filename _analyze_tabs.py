"""Analyze tab structure and field ownership in scan_dump.json."""
import json

with open("scan_dump.json") as f:
    scan = json.load(f)


def find_all(node, target_cls, results=None):
    if results is None:
        results = []
    if node.get("simpleClassName") == target_cls:
        results.append(node)
    for c in node.get("children", []):
        find_all(c, target_cls, results)
    return results


def count_actionable(node):
    cnt = 0
    stype = node.get("semanticType") or ""
    if stype in ("Field", "Button", "Checkbox", "ComboBox", "Tab"):
        cnt = 1
    for c in node.get("children", []):
        cnt += count_actionable(c)
    return cnt


def list_actionable_nodes(node, results=None):
    """Return actual node dicts, not just names."""
    if results is None:
        results = []
    stype = node.get("semanticType") or ""
    if stype in ("Field", "Button", "Checkbox", "ComboBox"):
        results.append(node)
    for c in node.get("children", []):
        list_actionable_nodes(c, results)
    return results


w = scan["windows"][0]
ef_nodes = find_all(w, "ExtendedFrame")
ef = [n for n in ef_nodes if "Global Find" in str(n.get("accessibleName", ""))][0]


def find_main_dp(node):
    if node.get("simpleClassName") == "DrawnPanel":
        children = node.get("children", [])
        has_tab = any(c.get("simpleClassName") == "FormsTabPanel" for c in children)
        if has_tab:
            return node
    for c in node.get("children", []):
        r = find_main_dp(c)
        if r:
            return r
    return None


dp = find_main_dp(ef)
if not dp:
    print("No main DrawnPanel found!")
    exit()

print("Main DrawnPanel children:")
for i, c in enumerate(dp.get("children", [])):
    cls = c.get("simpleClassName", "?")
    sb = c.get("screenBounds") or c.get("bounds") or {}
    nfields = count_actionable(c)
    print(f"  [{i}] {cls} y={sb.get('y','?')} h={sb.get('height','?')} fields={nfields}")

# Show tab info
tabbars = find_all(ef, "TabBar")
for tb in tabbars:
    attrs = tb.get("attributes", {})
    titles = attrs.get("tabTitles", "")
    selected = attrs.get("tabSelectedTitle", "")
    states = attrs.get("tabStates", "")
    print(f"\nTab titles: {titles}")
    print(f"Selected: {selected}")
    print(f"States: {states}")

# Focusable analysis for each FScrollBox
print("\nFocusable analysis:")
for i, c in enumerate(dp.get("children", [])):
    if c.get("simpleClassName") != "FScrollBox":
        continue
    sb = c.get("screenBounds") or {}
    fields = list_actionable_nodes(c)
    focusable = [f for f in fields if f.get("focusable")]
    non_foc = [f for f in fields if not f.get("focusable")]
    print(f"  FScrollBox[{i}] y={sb.get('y')} — {len(fields)} total,"
          f" {len(focusable)} focusable, {len(non_foc)} non-focusable")
    for f in focusable[:3]:
        print(f"    focusable: {f.get('accessibleName','')!r}")
    for f in non_foc[:3]:
        n = f.get("accessibleName", "") or f.get("name", "")
        print(f"    non-foc: {n!r} cls={f.get('simpleClassName','')}")
