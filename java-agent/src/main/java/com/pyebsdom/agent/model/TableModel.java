package com.pyebsdom.agent.model;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Value object representing one detected table / grid in the target JVM.
 *
 * <p>
 * Instances are produced by {@code extract.TableDetector} and serialised by
 * {@code render.DomJson}. Fields are public so the detector (a different
 * package) can build them incrementally and the renderer can read them; this
 * class itself carries no behaviour.
 *
 * <h3>Detection sources</h3>
 * <ul>
 * <li>{@code native-jtable} — a real {@code javax.swing.JTable}</li>
 * <li>{@code oracle-component} — class name indicates a known Oracle/ADF
 * table-like widget</li>
 * <li>{@code inferred-coordinate-grid} — fields inferred as a grid by shared-y
 * rows + shared-x columns</li>
 * <li>{@code inferred-repeated-fields} — fields named {@code ITEM_0},
 * {@code ITEM_1} etc.</li>
 * </ul>
 */
public final class TableModel {

    // ── Identity ──────────────────────────────────────────────────────────

    /** Unique id within a single scan result (1-based). */
    public int id;
    /** DOM path of the component from which this table was detected. */
    public String path = "";
    /** Component name ({@link java.awt.Component#getName()}). */
    public String name = "";
    /** Human-readable title / accessible name if available. */
    public String title = "";
    /** Detection source string (see class-level JavaDoc). */
    public String source = "";
    /** Confidence score [0.0 – 1.0]. */
    public double confidence = 0.0;

    // ── Structure ─────────────────────────────────────────────────────────

    /** Column header names in order. */
    public List<String> columns = new ArrayList<>();
    /**
     * Visible rows. Each row is an ordered map: column-name → cell-value.
     * The insertion order matches {@link #columns}.
     */
    public List<Map<String, String>> visibleRows = new ArrayList<>();

    // ── Diagnostics ───────────────────────────────────────────────────────

    /** Non-fatal issues encountered during detection. */
    public List<String> warnings = new ArrayList<>();

    public TableModel() {
    }

    @Override
    public String toString() {
        return "TableModel{id=" + id + ", source='" + source + "', name='" + name
                + "', cols=" + columns.size() + ", rows=" + visibleRows.size() + '}';
    }
}
