package com.pyebsdom.agent;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Value object representing one detected table / grid in the target JVM.
 *
 * <p>Instances are produced by {@link TableDetector} and serialised to JSON
 * by {@link #toJson()}.  All fields are intentionally package-accessible and
 * mutable so the detector can build them incrementally.
 *
 * <h3>Detection sources</h3>
 * <ul>
 *   <li>{@code native-jtable}          — a real {@code javax.swing.JTable}</li>
 *   <li>{@code oracle-component}       — class name indicates a known Oracle/ADF table-like widget</li>
 *   <li>{@code inferred-coordinate-grid} — fields inferred as a grid by shared-y rows + shared-x columns</li>
 *   <li>{@code inferred-repeated-fields} — fields named {@code ITEM_0}, {@code ITEM_1} etc.</li>
 * </ul>
 */
public final class TableModel {

    // ── Identity ──────────────────────────────────────────────────────────

    /** Unique id within a single scan result (1-based). */
    int    id;
    /** DOM path of the component from which this table was detected. */
    String path    = "";
    /** Component name ({@link java.awt.Component#getName()}). */
    String name    = "";
    /** Human-readable title / accessible name if available. */
    String title   = "";
    /** Detection source string (see class-level JavaDoc). */
    String source  = "";
    /** Confidence score [0.0 – 1.0]. */
    double confidence = 0.0;

    // ── Structure ─────────────────────────────────────────────────────────

    /** Column header names in order. */
    List<String>            columns     = new ArrayList<>();
    /**
     * Visible rows.  Each row is an ordered map: column-name → cell-value.
     * The insertion order matches {@link #columns}.
     */
    List<Map<String, String>> visibleRows = new ArrayList<>();

    // ── Diagnostics ───────────────────────────────────────────────────────

    /** Non-fatal issues encountered during detection. */
    List<String> warnings = new ArrayList<>();

    // ── Construction ──────────────────────────────────────────────────────

    TableModel() {}

    // ── Serialisation ─────────────────────────────────────────────────────

    /**
     * Serialises this table model to a compact (single-line) JSON object.
     */
    public String toJson() {
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"id\":").append(id).append(',');
        sb.append("\"path\":").append(JsonUtil.quoted(path)).append(',');
        sb.append("\"name\":").append(JsonUtil.quoted(name)).append(',');
        sb.append("\"title\":").append(JsonUtil.quoted(title)).append(',');
        sb.append("\"source\":").append(JsonUtil.quoted(source)).append(',');
        sb.append("\"confidence\":").append(String.format("%.2f", confidence)).append(',');

        // columns
        sb.append("\"columns\":[");
        for (int i = 0; i < columns.size(); i++) {
            if (i > 0) sb.append(',');
            sb.append(JsonUtil.quoted(columns.get(i)));
        }
        sb.append("],");

        // visibleRows
        sb.append("\"visibleRows\":[");
        for (int r = 0; r < visibleRows.size(); r++) {
            if (r > 0) sb.append(',');
            Map<String, String> row = visibleRows.get(r);
            sb.append('{');
            boolean firstCell = true;
            for (Map.Entry<String, String> cell : row.entrySet()) {
                if (!firstCell) sb.append(',');
                sb.append(JsonUtil.quoted(cell.getKey()))
                  .append(':')
                  .append(JsonUtil.quoted(cell.getValue()));
                firstCell = false;
            }
            sb.append('}');
        }
        sb.append("],");

        // warnings
        sb.append("\"warnings\":[");
        for (int i = 0; i < warnings.size(); i++) {
            if (i > 0) sb.append(',');
            sb.append(JsonUtil.quoted(warnings.get(i)));
        }
        sb.append(']');

        sb.append('}');
        return sb.toString();
    }

    @Override
    public String toString() {
        return "TableModel{id=" + id + ", source='" + source + "', name='" + name
                + "', cols=" + columns.size() + ", rows=" + visibleRows.size() + '}';
    }
}
