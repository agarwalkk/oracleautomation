package com.pyebsdom.agent;

import java.util.Collections;
import java.util.Map;

/**
 * Parsed representation of a single agent invocation.
 *
 * <p>Instances are created exclusively by {@link AgentCommandParser}; they are
 * immutable once constructed.
 *
 * <p>All key names are lower-cased by the parser so that callers need not
 * worry about casing ({@code OUT}, {@code Out}, and {@code out} all map to the
 * same entry).
 */
public final class AgentCommand {

    private final String              command;
    private final String              outputFile;
    private final Map<String, String> params;

    AgentCommand(String command, String outputFile, Map<String, String> params) {
        this.command    = command;
        this.outputFile = outputFile;
        this.params     = Collections.unmodifiableMap(params);
    }

    /** The value of the {@code command} key (already lower-cased). */
    public String getCommand() {
        return command;
    }

    /** The value of the {@code out} key — absolute path for the JSON result file. */
    public String getOutputFile() {
        return outputFile;
    }

    /** All parsed key=value pairs, including {@code command} and {@code out}. */
    public Map<String, String> getParams() {
        return params;
    }

    /**
     * Returns the value for {@code key}, or {@code null} if absent.
     * Key lookup is case-sensitive (keys are stored lower-cased by the parser).
     */
    public String getParam(String key) {
        return params.get(key);
    }

    /**
     * Returns the value for {@code key}, or {@code defaultValue} if absent.
     */
    public String getParam(String key, String defaultValue) {
        String v = params.get(key);
        return v != null ? v : defaultValue;
    }

    @Override
    public String toString() {
        return "AgentCommand{command='" + command + "', out='" + outputFile + "', params=" + params + '}';
    }
}
