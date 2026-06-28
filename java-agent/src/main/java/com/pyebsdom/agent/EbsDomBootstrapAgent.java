package com.pyebsdom.agent;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.lang.instrument.Instrumentation;
import java.lang.reflect.Method;
import java.net.URL;
import java.net.URLClassLoader;

/**
 * Minimal manifest entry point for dynamic attach.
 *
 * <h3>Fresh-load loop (no Forms restart per change)</h3>
 * Java dynamic attach loads agent classes into the target JVM's system class
 * loader, where they stay <b>cached for the JVM's lifetime</b>. With the old
 * {@code Class.forName} approach, editing the agent and rebuilding the jar had
 * NO effect until the Oracle Forms JVM was restarted.
 *
 * <p>
 * This bootstrap instead loads the real agent through a fresh, child-first
 * {@link URLClassLoader} over the jar on <b>every</b> attach, so a rebuilt jar
 * takes effect immediately — your loop becomes "rebuild → re-run". The loader
 * is closed after the command so the jar file handle is released (important on
 * Windows). {@code AttachAgentByPid} loads a throwaway temp copy of the jar, so
 * the original is never locked and can be overwritten by Maven while Forms
 * runs.
 *
 * <p>
 * Only changes to THIS bootstrap class still require one Forms restart (it is
 * the Agent-Class and is itself cached). Everything else — EbsDomAgent,
 * DomScanner, FormsHandler, ReflectionProbe, … — reloads fresh.
 *
 * <p>
 * If the jar path can't be resolved, it falls back to the legacy cached
 * {@code Class.forName} load, so it can never be worse than before.
 */
public final class EbsDomBootstrapAgent {

    private EbsDomBootstrapAgent() {
    }

    public static void premain(String agentArgs, Instrumentation inst) {
        run(agentArgs, inst);
    }

    public static void agentmain(String agentArgs, Instrumentation inst) {
        run(agentArgs, inst);
    }

    private static void run(String agentArgs, Instrumentation inst) {
        System.err.println("[ebs-dom-agent] bootstrap entered (fresh-loader)");
        try {
            String jarPath = resolveJarPath(agentArgs);
            if (jarPath == null) {
                System.err.println("[ebs-dom-agent] jar path unresolved; using cached load");
                legacyRun(agentArgs, inst);
                return;
            }
            URL[] urls = { new File(jarPath).toURI().toURL() };
            // try-with-resources closes the loader after the command, releasing
            // the (temp) jar handle.
            try (FreshAgentLoader cl = new FreshAgentLoader(
                    urls, EbsDomBootstrapAgent.class.getClassLoader())) {
                Class<?> agentClass = cl.loadClass("com.pyebsdom.agent.EbsDomAgent");
                Method runCommand = agentClass.getDeclaredMethod(
                        "runCommand", String.class, Instrumentation.class);
                runCommand.setAccessible(true);
                runCommand.invoke(null, agentArgs, inst);
            }
        } catch (Throwable t) {
            Throwable root = rootCause(t);
            System.err.println("[ebs-dom-agent] bootstrap failed: "
                    + root.getClass().getName() + ": " + root.getMessage());
            root.printStackTrace(System.err);
            writeFallbackError(agentArgs, root);
        }
    }

    /**
     * Legacy cached load (system class loader). Used only if the jar path is
     * unknown.
     */
    private static void legacyRun(String agentArgs, Instrumentation inst) throws Exception {
        Class<?> agentClass = Class.forName("com.pyebsdom.agent.EbsDomAgent");
        Method runCommand = agentClass.getDeclaredMethod(
                "runCommand", String.class, Instrumentation.class);
        runCommand.setAccessible(true);
        runCommand.invoke(null, agentArgs, inst);
    }

    /**
     * Resolve the jar to fresh-load from: prefer the {@code agentjar=} arg that
     * {@link AttachAgentByPid} threads (the actual loaded temp jar), then fall
     * back to this class's own code source.
     */
    private static String resolveJarPath(String agentArgs) {
        String fromArg = extract(agentArgs, "agentjar");
        if (fromArg != null && new File(fromArg).isFile())
            return fromArg;
        try {
            java.security.CodeSource cs = EbsDomBootstrapAgent.class.getProtectionDomain().getCodeSource();
            if (cs != null && cs.getLocation() != null) {
                File f = new File(cs.getLocation().toURI());
                if (f.isFile())
                    return f.getAbsolutePath();
            }
        } catch (Throwable ignored) {
        }
        return null;
    }

    /**
     * Child-first loader for {@code com.pyebsdom.agent.*}: the agent's own
     * classes are loaded FRESH from the jar each attach; everything else
     * (java.*, javax.*, oracle.*) delegates to the parent as usual, so the
     * fresh agent still interoperates with the live Forms component tree.
     */
    static final class FreshAgentLoader extends URLClassLoader {
        FreshAgentLoader(URL[] urls, ClassLoader parent) {
            super(urls, parent);
        }

        @Override
        protected Class<?> loadClass(String name, boolean resolve) throws ClassNotFoundException {
            synchronized (getClassLoadingLock(name)) {
                // Never reload THIS bootstrap (it is the currently-running shim);
                // load every other agent class fresh from the jar.
                if (name.startsWith("com.pyebsdom.agent.")
                        && !name.equals("com.pyebsdom.agent.EbsDomBootstrapAgent")) {
                    Class<?> c = findLoadedClass(name);
                    if (c == null) {
                        try {
                            c = findClass(name); // from the jar URL → fresh
                        } catch (ClassNotFoundException e) {
                            c = super.loadClass(name, false); // fall back to parent
                        }
                    }
                    if (resolve)
                        resolveClass(c);
                    return c;
                }
                return super.loadClass(name, resolve);
            }
        }
    }

    // ── helpers (unchanged from the original) ─────────────────────────────

    private static Throwable rootCause(Throwable t) {
        Throwable current = t;
        while (current.getCause() != null) {
            current = current.getCause();
        }
        return current;
    }

    private static String extract(String agentArgs, String key) {
        if (agentArgs == null)
            return null;
        for (String part : agentArgs.split(";")) {
            int idx = part.indexOf('=');
            if (idx <= 0)
                continue;
            if (key.equalsIgnoreCase(part.substring(0, idx).trim())) {
                return part.substring(idx + 1).trim();
            }
        }
        return null;
    }

    private static void writeFallbackError(String agentArgs, Throwable t) {
        String out = extract(agentArgs, "out");
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
        }
    }

    private static String quote(String value) {
        if (value == null)
            return "null";
        StringBuilder sb = new StringBuilder(value.length() + 16);
        sb.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"':
                    sb.append("\\\"");
                    break;
                case '\\':
                    sb.append("\\\\");
                    break;
                case '\b':
                    sb.append("\\b");
                    break;
                case '\f':
                    sb.append("\\f");
                    break;
                case '\n':
                    sb.append("\\n");
                    break;
                case '\r':
                    sb.append("\\r");
                    break;
                case '\t':
                    sb.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        String hex = Integer.toHexString(c);
                        sb.append("\\u");
                        for (int j = hex.length(); j < 4; j++)
                            sb.append('0');
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
