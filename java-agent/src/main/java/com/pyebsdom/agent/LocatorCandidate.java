package com.pyebsdom.agent;

/**
 * A single candidate locator expression that can be used to re-identify
 * a component in a future automation step.
 *
 * <p>
 * Each locator has a {@link #strategy} (e.g. {@code "name"},
 * {@code "text"}, {@code "path"}, {@code "semanticId"}, {@code "treePath"}),
 * a {@link #value} (the actual string needed to locate the component), and a
 * {@link #confidence} score in {@code [0.0, 1.0]}.
 *
 * <p>
 * {@link #verifiedUnique} is set by {@link IdentityResolver} when the agent
 * has confirmed, against the live component tree, that this locator resolves to
 * exactly one component. Replay must prefer verified-unique locators; a
 * locator that is not verified-unique may silently match the wrong widget
 * (e.g. the first of many "Order Number" cells in a grid).
 *
 * <p>
 * {@link #scope} optionally narrows resolution to a container subtree
 * (a tab page, block, or grid) so that a non-globally-unique label becomes
 * unique within its scope. It carries the scope's own semanticId.
 */
public final class LocatorCandidate {

    /** Strategy key — short identifier for how to apply the locator. */
    public final String strategy;

    /** The value to match against. */
    public final String value;

    /**
     * Confidence in {@code [0.0, 1.0]}. Higher is better. When
     * {@link #verifiedUnique} is true this is forced to 1.0.
     */
    public final double confidence;

    /** True when the agent verified this resolves to exactly one component. */
    public final boolean verifiedUnique;

    /** Optional container scope semanticId; {@code null} for global locators. */
    public final String scope;

    /**
     * Optional ordinal within a repeating container (record/row index); -1 if N/A.
     */
    public final int ordinal;

    public LocatorCandidate(String strategy, String value, double confidence) {
        this(strategy, value, confidence, false, null, -1);
    }

    public LocatorCandidate(String strategy, String value, double confidence,
            boolean verifiedUnique, String scope, int ordinal) {
        this.strategy = strategy;
        this.value = value;
        this.verifiedUnique = verifiedUnique;
        this.confidence = verifiedUnique ? 1.0 : clamp(confidence);
        this.scope = scope;
        this.ordinal = ordinal;
    }

    /** Returns this locator as a JSON object string. */
    public String toJson() {
        StringBuilder sb = new StringBuilder("{");
        sb.append("\"strategy\":").append(JsonUtil.quoted(strategy)).append(',');
        sb.append("\"value\":").append(JsonUtil.quoted(value)).append(',');
        sb.append("\"confidence\":").append(String.format("%.2f", confidence)).append(',');
        sb.append("\"verifiedUnique\":").append(verifiedUnique);
        if (scope != null) {
            sb.append(",\"scope\":").append(JsonUtil.quoted(scope));
        }
        if (ordinal >= 0) {
            sb.append(",\"ordinal\":").append(ordinal);
        }
        sb.append('}');
        return sb.toString();
    }

    @Override
    public String toString() {
        return "LocatorCandidate{" + strategy + "=" + value
                + (verifiedUnique ? " UNIQUE" : "")
                + " (" + String.format("%.0f%%", confidence * 100) + ")}";
    }

    private static double clamp(double v) {
        return Math.max(0.0, Math.min(1.0, v));
    }
}
