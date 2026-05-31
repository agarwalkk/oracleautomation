package com.pyebsdom.agent;

/**
 * A single candidate locator expression that can be used to re-identify
 * a component in a future automation step.
 *
 * <p>Each locator has a {@link #strategy} (e.g. {@code "name"},
 * {@code "text"}, {@code "path"}), a {@link #value} (the actual string
 * needed to locate the component), and a {@link #confidence} score in
 * {@code [0.0, 1.0]} indicating how reliable the locator is expected to be.
 *
 * <p>Higher confidence means the locator is more stable across EBS sessions
 * or navigation events.  A score of {@code 1.0} means "this is as unique and
 * stable as it gets"; {@code 0.3} means "may work but may also match multiple
 * components or change between sessions".
 */
public final class LocatorCandidate {

    /** Strategy key — short identifier for how to apply the locator. */
    public final String strategy;

    /** The value to match against. */
    public final String value;

    /**
     * Confidence in {@code [0.0, 1.0]}.
     * Higher is better.
     */
    public final double confidence;

    public LocatorCandidate(String strategy, String value, double confidence) {
        this.strategy   = strategy;
        this.value      = value;
        this.confidence = clamp(confidence);
    }

    /** Returns this locator as a JSON object string. */
    public String toJson() {
        return "{"
             + "\"strategy\":"   + JsonUtil.quoted(strategy)  + ","
             + "\"value\":"      + JsonUtil.quoted(value)      + ","
             + "\"confidence\":" + String.format("%.2f", confidence)
             + "}";
    }

    @Override
    public String toString() {
        return "LocatorCandidate{" + strategy + "=" + value
                + " (" + String.format("%.0f%%", confidence * 100) + ")}";
    }

    private static double clamp(double v) {
        return Math.max(0.0, Math.min(1.0, v));
    }
}
