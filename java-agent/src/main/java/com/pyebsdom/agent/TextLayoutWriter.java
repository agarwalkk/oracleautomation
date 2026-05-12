package com.pyebsdom.agent;

import java.util.List;
import java.util.Map;

/**
 * Renders a {@link DomScanner.ScanResult} as a human-readable plain-text
 * layout report.
 *
 * <p>Output format:
 * <pre>
 * Oracle EBS Java DOM Layout
 * ==========================
 * Timestamp : 2026-05-08T12:00:00Z
 * Windows   : 3 total, 2 visible
 * Raw mode  : false
 *
 * Window [1/3]: My Window Title
 *   Panel [1/5]: contentPane
 *     Field [1/3]: ITEM_NUM
 *     Field [2/3]: ITEM_DESC
 *     Button [3/3]: Save
 *   ...
 * </pre>
 *
 * <p>The indent step is two spaces per depth level.  The depth shown in
 * the layout is <em>relative</em> to the window root (window = depth 0).
 */
public final class TextLayoutWriter {

    private static final String INDENT = "  ";
    private static final int    MAX_TEXT_LEN = 60;

    private TextLayoutWriter() {}

    /**
     * Generates the full layout text for the given scan result.
     *
     * @param result a completed {@link DomScanner.ScanResult}
     * @return multi-line UTF-8 string (lines separated by {@code \n})
     */
    public static String write(DomScanner.ScanResult result) {
        StringBuilder sb = new StringBuilder();

        // ── Header ────────────────────────────────────────────────────────
        sb.append("Oracle EBS Java DOM Layout\n");
        sb.append("==========================\n");
        sb.append("Timestamp : ").append(result.timestamp).append('\n');
        sb.append("Windows   : ").append(result.windowCount)
          .append(" total, ").append(result.visibleWindowCount).append(" visible\n");
        sb.append("Raw mode  : ").append(result.raw).append('\n');
        sb.append('\n');

        if (result.windows.isEmpty()) {
            sb.append("(no windows found)\n");
            return sb.toString();
        }

        // ── Windows ───────────────────────────────────────────────────────
        int total = result.windows.size();
        for (int i = 0; i < total; i++) {
            DomNode window = result.windows.get(i);
            sb.append("Window [").append(i + 1).append('/').append(total)
              .append("]: ").append(windowLabel(window)).append('\n');

            writeChildren(sb, window.children, 1);
            sb.append('\n');
        }

        return sb.toString();
    }

    // ── Recursive child rendering ─────────────────────────────────────────

    private static void writeChildren(StringBuilder sb,
                                      List<DomNode> children,
                                      int relativeDepth) {
        if (children == null || children.isEmpty()) return;

        int total = children.size();
        for (int i = 0; i < total; i++) {
            DomNode node = children.get(i);
            writeNode(sb, node, relativeDepth, i + 1, total);
            writeChildren(sb, node.children, relativeDepth + 1);
        }
    }

    private static void writeNode(StringBuilder sb,
                                  DomNode node,
                                  int relativeDepth,
                                  int oneBasedIndex,
                                  int siblingTotal) {
        // Indent
        for (int d = 0; d < relativeDepth; d++) {
            sb.append(INDENT);
        }

        // Type label
        String semantic = node.semanticType != null ? node.semanticType : "?";
        sb.append(semantic);

        // Position in siblings
        sb.append(" [").append(oneBasedIndex).append('/').append(siblingTotal).append("]: ");

        // Display name
        String label = buildLabel(node);
        sb.append(label);

        // State hints (compact, in parentheses)
        String hints = stateHints(node);
        if (!hints.isEmpty()) {
            sb.append("  (").append(hints).append(')');
        }

        // Bounds summary
        if (node.bounds != null) {
            sb.append("  [")
              .append(node.bounds.width).append('x').append(node.bounds.height)
              .append(" @ ").append(node.bounds.x).append(',').append(node.bounds.y)
              .append(']');
        }

        sb.append('\n');
    }

    // ── Label helpers ─────────────────────────────────────────────────────

    private static String windowLabel(DomNode win) {
        if (notBlank(win.title))         return win.title;
        if (notBlank(win.accessibleName)) return win.accessibleName;
        if (notBlank(win.name))          return win.name;
        return win.simpleClassName != null ? win.simpleClassName : "(untitled)";
    }

    private static String buildLabel(DomNode node) {
        // Prefer the resolved displayName
        String label = notBlank(node.displayName) ? node.displayName : null;
        if (label == null) label = notBlank(node.accessibleName) ? node.accessibleName : null;
        if (label == null) label = notBlank(node.name)           ? node.name           : null;
        if (label == null) label = notBlank(node.title)          ? node.title          : null;
        if (label == null) label = notBlank(node.text)           ? node.text           : null;
        if (label == null) label = node.simpleClassName          != null
                                   ? node.simpleClassName : "(no label)";

        // Truncate very long values
        if (label.length() > MAX_TEXT_LEN) {
            label = label.substring(0, MAX_TEXT_LEN) + "…";
        }
        return label;
    }

    private static String stateHints(DomNode node) {
        StringBuilder hints = new StringBuilder();
        if (!node.visible)  append(hints, "hidden");
        if (!node.enabled)  append(hints, "disabled");
        if (node.focused)   append(hints, "focused");
        if (node.selected)  append(hints, "selected");
        if (node.editable)  append(hints, "editable");
        if (notBlank(node.value)) {
            String v = node.value.length() > 20
                       ? node.value.substring(0, 20) + "…" : node.value;
            append(hints, "value=" + v);
        }
        return hints.toString();
    }

    private static void append(StringBuilder sb, String token) {
        if (sb.length() > 0) sb.append(", ");
        sb.append(token);
    }

    private static boolean notBlank(String s) {
        return s != null && !s.trim().isEmpty() && !"null".equals(s);
    }

    // ── Table summary section ─────────────────────────────────────────────

    /**
     * Appends a plain-text summary of detected tables to the layout builder.
     *
     * <p>Called from {@link #write(DomScanner.ScanResult)} when the caller
     * also provides a table list (or can be invoked independently).
     */
    public static String writeTables(List<TableModel> tables) {
        StringBuilder sb = new StringBuilder();
        sb.append("Detected Tables\n");
        sb.append("===============\n");

        if (tables == null || tables.isEmpty()) {
            sb.append("(no tables detected)\n");
            return sb.toString();
        }

        sb.append("Count: ").append(tables.size()).append('\n').append('\n');

        for (TableModel tm : tables) {
            sb.append("Table #").append(tm.id)
              .append("  [").append(tm.source).append(']')
              .append("  confidence=").append(String.format("%.2f", tm.confidence))
              .append('\n');
            if (notBlank(tm.name))  sb.append("  Name  : ").append(tm.name).append('\n');
            if (notBlank(tm.title)) sb.append("  Title : ").append(tm.title).append('\n');
            sb.append("  Path  : ").append(tm.path).append('\n');
            sb.append("  Cols  : ").append(tm.columns.isEmpty() ? "(none)" : String.join(", ", tm.columns)).append('\n');
            sb.append("  Rows  : ").append(tm.visibleRows.size()).append('\n');

            // Print up to 3 sample rows
            int sample = Math.min(tm.visibleRows.size(), 3);
            for (int r = 0; r < sample; r++) {
                Map<String, String> row = tm.visibleRows.get(r);
                sb.append("    row[").append(r).append("]: ");
                boolean first = true;
                for (Map.Entry<String, String> cell : row.entrySet()) {
                    if (!first) sb.append(", ");
                    sb.append(cell.getKey()).append('=').append(cell.getValue());
                    first = false;
                }
                sb.append('\n');
            }
            if (tm.visibleRows.size() > sample) {
                sb.append("    ... (").append(tm.visibleRows.size() - sample)
                  .append(" more row(s))\n");
            }

            if (!tm.warnings.isEmpty()) {
                for (String w : tm.warnings) {
                    sb.append("  WARN: ").append(w).append('\n');
                }
            }
            sb.append('\n');
        }

        return sb.toString();
    }
}
