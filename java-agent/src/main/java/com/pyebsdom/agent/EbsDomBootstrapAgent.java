package com.pyebsdom.agent;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.lang.instrument.Instrumentation;
import java.lang.reflect.Method;

/**
 * Minimal manifest entry point for dynamic attach.
 *
 * This class intentionally has almost no dependencies so Java 8 can load it
 * even if the full agent has a verification/linkage problem. It then reflects
 * into {@link EbsDomAgent} and delegates the real command execution.
 */
public final class EbsDomBootstrapAgent {

    private EbsDomBootstrapAgent() {}

    public static void premain(String agentArgs, Instrumentation inst) {
        run(agentArgs, inst);
    }

    public static void agentmain(String agentArgs, Instrumentation inst) {
        run(agentArgs, inst);
    }

    private static void run(String agentArgs, Instrumentation inst) {
        System.err.println("[ebs-dom-agent] bootstrap entered");
        try {
            Class<?> agentClass = Class.forName("com.pyebsdom.agent.EbsDomAgent");
            Method runCommand = agentClass.getDeclaredMethod(
                    "runCommand", String.class, Instrumentation.class);
            runCommand.invoke(null, agentArgs, inst);
        } catch (Throwable t) {
            Throwable root = rootCause(t);
            System.err.println("[ebs-dom-agent] bootstrap failed: "
                    + root.getClass().getName() + ": " + root.getMessage());
            root.printStackTrace(System.err);
            writeFallbackError(agentArgs, root);
        }
    }

    private static Throwable rootCause(Throwable t) {
        Throwable current = t;
        while (current.getCause() != null) {
            current = current.getCause();
        }
        return current;
    }

    private static void writeFallbackError(String agentArgs, Throwable t) {
        String out = extractOut(agentArgs);
        if (out == null || out.length() == 0) {
            return;
        }

        File file = new File(out);
        File parent = file.getParentFile();
        if (parent != null && !parent.exists()) {
            parent.mkdirs();
        }

        StringWriter sw = new StringWriter();
        t.printStackTrace(new PrintWriter(sw));

        String json = "{"
                + "\"status\":\"error\","
                + "\"command\":\"bootstrap\","
                + "\"message\":" + quote(t.getClass().getName() + ": " + t.getMessage()) + ","
                + "\"exception\":" + quote(t.getClass().getName()) + ","
                + "\"stackTrace\":" + quote(sw.toString())
                + "}";

        try (OutputStreamWriter writer = new OutputStreamWriter(
                new FileOutputStream(file), "UTF-8")) {
            writer.write(json);
        } catch (Exception ignored) {
            // Nothing else to do. The attach launcher will report failure.
        }
    }

    private static String extractOut(String agentArgs) {
        if (agentArgs == null) return null;
        String[] parts = agentArgs.split(";");
        for (String part : parts) {
            int idx = part.indexOf('=');
            if (idx <= 0) continue;
            String key = part.substring(0, idx).trim().toLowerCase();
            if ("out".equals(key)) {
                return part.substring(idx + 1).trim();
            }
        }
        return null;
    }

    private static String quote(String value) {
        if (value == null) return "null";
        StringBuilder sb = new StringBuilder(value.length() + 16);
        sb.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\b': sb.append("\\b"); break;
                case '\f': sb.append("\\f"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        String hex = Integer.toHexString(c);
                        sb.append("\\u");
                        for (int j = hex.length(); j < 4; j++) sb.append('0');
                        sb.append(hex);
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append('"');
        return sb.toString();
    }
}
