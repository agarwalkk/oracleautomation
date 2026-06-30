package com.pyebsdom.agent.json;

/**
 * Minimal JSON string helpers — the single source of truth for escaping and
 * quoting across the agent.
 *
 * <p>No external JSON library is used so the agent JAR stays small and avoids
 * classloader conflicts inside the target Oracle Forms JVM. Renderers
 * ({@code render.DomJson}, {@code render.TextLayout}) and the action layer
 * build their payloads with {@link StringBuilder} and call {@link #quoted}
 * for every string value.
 */
public final class Json {

    private Json() { /* static utility */ }

    /**
     * Returns {@code value} as a JSON double-quoted string (including the
     * surrounding quotes), or the literal {@code null} when {@code value} is
     * {@code null}.
     */
    public static String quoted(String value) {
        if (value == null) return "null";
        return '"' + escape(value) + '"';
    }

    /**
     * Escapes a string for safe embedding inside a JSON double-quoted value.
     * Handles all RFC 8259 mandatory escape sequences. A {@code null} input
     * returns the empty string (no quotes).
     */
    public static String escape(String s) {
        if (s == null) return "";
        StringBuilder out = new StringBuilder(s.length() + 8);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':  out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\b': out.append("\\b");  break;
                case '\f': out.append("\\f");  break;
                case '\n': out.append("\\n");  break;
                case '\r': out.append("\\r");  break;
                case '\t': out.append("\\t");  break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        return out.toString();
    }

    /** Formats a double with two decimals, locale-independent (e.g. confidence). */
    public static String decimal2(double v) {
        return String.format(java.util.Locale.ROOT, "%.2f", v);
    }
}
