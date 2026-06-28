package com.pyebsdom.agent;

import java.io.DataInputStream;
import java.io.File;
import java.io.IOException;
import java.lang.reflect.Method;
import java.util.Properties;
import java.util.jar.Attributes;
import java.util.jar.JarFile;
import java.util.jar.Manifest;

/**
 * One-shot Attach helper for pyebsdom.
 *
 * <p>
 * Attaches to a running JVM process, loads {@code ebs-dom-agent.jar} into
 * it with the given argument string, then immediately detaches.
 *
 * <p>
 * {@link VirtualMachine#loadAgent(String, String)} is synchronous: it does
 * not return until {@code agentmain} has finished executing inside the target
 * JVM. This means the JSON output file is fully written before this process
 * exits with code 0.
 *
 * <h3>Usage</h3>
 *
 * <b>Java 8</b>
 * 
 * <pre>
 *   java -cp ebs-dom-agent.jar:$JAVA_HOME/lib/tools.jar \
 *        com.pyebsdom.agent.AttachAgentByPid \
 *        &lt;pid&gt; &lt;agentJar&gt; &lt;agentArgs&gt;
 * </pre>
 *
 * <b>Java 9+</b>
 * 
 * <pre>
 *   java --add-modules jdk.attach \
 *        -cp ebs-dom-agent.jar \
 *        com.pyebsdom.agent.AttachAgentByPid \
 *        &lt;pid&gt; &lt;agentJar&gt; &lt;agentArgs&gt;
 * </pre>
 *
 * <b>Windows example</b>
 * 
 * <pre>
 *   java -cp ebs-dom-agent.jar;%JAVA_HOME%\lib\tools.jar ^
 *        com.pyebsdom.agent.AttachAgentByPid ^
 *        12345 C:\pyebsdom\ebs-dom-agent.jar ^
 *        "command=health;out=C:\Temp\pyebsdom-health.json"
 * </pre>
 *
 * <h3>Exit codes</h3>
 * <ul>
 * <li>{@code 0} — agent loaded and executed (result written to {@code out}
 * file).</li>
 * <li>{@code 1} — wrong number of arguments.</li>
 * <li>{@code 2} — attach or load-agent failure.</li>
 * </ul>
 *
 * <h3>Permissions</h3>
 * The Attach API requires that the attaching process runs as the same OS user
 * as the target JVM, or as a privileged (root / Administrator) user. On Java
 * 17+ you may also need {@code -Djdk.attach.allowAttachSelf=true} when the
 * target PID is the current process (useful in unit tests).
 */
public final class AttachAgentByPid {

    private AttachAgentByPid() {
        /* main-class entry point only */ }

    public static void main(String[] args) {
        if (args.length < 3) {
            printUsage();
            System.exit(1);
        }

        String pid = args[0].trim();
        String agentJar = args[1].trim();
        String agentArgs = args[2]; // preserve original spacing / casing

        System.out.println("[ebs-dom-agent] ─────────────────────────────────────");
        System.out.println("[ebs-dom-agent] Attaching to JVM PID : " + pid);
        System.out.println("[ebs-dom-agent] Agent JAR            : " + agentJar);
        System.out.println("[ebs-dom-agent] Agent args           : " + agentArgs);
        System.out.println("[ebs-dom-agent] ─────────────────────────────────────");

        Object vm = null;
        try {

            // ── 1. Attach ─────────────────────────────────────────────────
            Class<?> vmClass = Class.forName("com.sun.tools.attach.VirtualMachine");
            Method attachMethod = vmClass.getMethod("attach", String.class);
            vm = attachMethod.invoke(null, pid);
            System.out.println("[ebs-dom-agent] Attached successfully.");

            printPreLoadDiagnostics(vmClass, vm, agentJar);

            // ── 2. Load agent (blocks until agentmain returns) ────────────
            // Load a throwaway COPY so the ORIGINAL jar is never locked by the
            // target JVM → you can `mvn package` over it while Forms runs.
            // Copy into the original's OWN directory: it is readable by the
            // target (that's where the real jar lives) and free of the per-user
            // ACL / space issues that make %TEMP% fail to load.
            String loadJar = agentJar;
            try {
                java.io.File origin = new java.io.File(agentJar).getAbsoluteFile();
                java.io.File dir = origin.getParentFile();

                // Best-effort sweep of stale copies (in-use ones fail to delete
                // and are simply skipped).
                java.io.File[] stale = dir.listFiles();
                if (stale != null) {
                    for (java.io.File s : stale) {
                        String n = s.getName();
                        if (n.startsWith("ebs-dom-agent.attach-") && n.endsWith(".jar")) {
                            try {
                                s.delete();
                            } catch (Exception ignore) {
                            }
                        }
                    }
                }

                java.io.File copy = new java.io.File(dir,
                        "ebs-dom-agent.attach-" + System.nanoTime() + ".jar");
                java.nio.file.Files.copy(origin.toPath(), copy.toPath(),
                        java.nio.file.StandardCopyOption.REPLACE_EXISTING);

                if (copy.isFile() && copy.length() > 0) {
                    copy.deleteOnExit();
                    loadJar = copy.getAbsolutePath();
                    System.out.println("[ebs-dom-agent] Load copy : " + loadJar
                            + " (" + copy.length() + " bytes)");
                } else {
                    System.err.println("[ebs-dom-agent] copy invalid; loading original jar");
                }
            } catch (Exception copyEx) {
                System.err.println("[ebs-dom-agent] copy failed; loading original jar: "
                        + copyEx.getMessage());
            }

            String effectiveArgs = (agentArgs == null ? "" : agentArgs)
                    + ";agentjar=" + loadJar;

            Method loadAgentMethod = vmClass.getMethod("loadAgent", String.class, String.class);
            loadAgentMethod.invoke(vm, loadJar, effectiveArgs);
            System.out.println("[ebs-dom-agent] Agent executed successfully.");

        } catch (Exception e) {
            Throwable root = rootCause(e);
            if ("com.sun.tools.attach.AgentLoadException".equals(root.getClass().getName())
                    && "0".equals(String.valueOf(root.getMessage()))) {
                System.err.println("[ebs-dom-agent] Warning: loadAgent reported AgentLoadException: 0; "
                        + "treating as success because the target JVM returned code 0.");
            } else {
                System.err.println("[ebs-dom-agent] FAILED: "
                        + root.getClass().getName() + ": " + root.getMessage());
                root.printStackTrace(System.err);
                System.exit(2);
            }

        } finally {
            if (vm != null) {
                try {
                    Class<?> vmClass = Class.forName("com.sun.tools.attach.VirtualMachine");
                    vmClass.getMethod("detach").invoke(vm);
                    System.out.println("[ebs-dom-agent] Detached.");
                } catch (Exception detachEx) {
                    // Non-fatal: target JVM may have already exited.
                    System.err.println("[ebs-dom-agent] Warning: detach failed ("
                            + detachEx.getMessage() + ")");
                }
            }
        }

        System.out.println("[ebs-dom-agent] Done.");
    }

    private static Throwable rootCause(Throwable t) {
        Throwable current = t;
        while (current.getCause() != null) {
            current = current.getCause();
        }
        return current;
    }

    private static void printPreLoadDiagnostics(Class<?> vmClass, Object vm, String agentJar) {
        System.out.println("[ebs-dom-agent] Pre-load diagnostics:");

        try {
            Method getSystemProperties = vmClass.getMethod("getSystemProperties");
            Properties props = (Properties) getSystemProperties.invoke(vm);
            printProp(props, "java.version");
            printProp(props, "java.vendor");
            printProp(props, "java.vm.name");
            printProp(props, "java.vm.version");
            printProp(props, "java.home");
            printProp(props, "os.name");
            printProp(props, "sun.arch.data.model");
        } catch (Exception e) {
            System.out.println("[ebs-dom-agent]   target properties unavailable: "
                    + e.getClass().getName() + ": " + e.getMessage());
        }

        File jarFile = new File(agentJar);
        System.out.println("[ebs-dom-agent]   jar.exists=" + jarFile.isFile());
        System.out.println("[ebs-dom-agent]   jar.absolute=" + jarFile.getAbsolutePath());
        System.out.println("[ebs-dom-agent]   jar.length=" + jarFile.length());

        if (jarFile.isFile()) {
            try (JarFile jar = new JarFile(jarFile)) {
                Manifest manifest = jar.getManifest();
                if (manifest != null) {
                    Attributes attrs = manifest.getMainAttributes();
                    System.out.println("[ebs-dom-agent]   manifest.Agent-Class="
                            + attrs.getValue("Agent-Class"));
                    System.out.println("[ebs-dom-agent]   manifest.Premain-Class="
                            + attrs.getValue("Premain-Class"));
                } else {
                    System.out.println("[ebs-dom-agent]   manifest=<missing>");
                }
            } catch (Exception e) {
                System.out.println("[ebs-dom-agent]   manifest read failed: "
                        + e.getClass().getName() + ": " + e.getMessage());
            }

            printClassMajor(agentJar, "com.pyebsdom.agent.EbsDomAgent");
            printClassMajor(agentJar, "com.pyebsdom.agent.ActionExecutor");
            printClassMajor(agentJar, "com.pyebsdom.agent.AttachAgentByPid");
        }
    }

    private static void printProp(Properties props, String key) {
        System.out.println("[ebs-dom-agent]   target." + key + "=" + props.getProperty(key));
    }

    private static void printClassMajor(String agentJar, String className) {
        String entryName = className.replace('.', '/') + ".class";
        try (JarFile jar = new JarFile(agentJar)) {
            if (jar.getEntry(entryName) == null) {
                System.out.println("[ebs-dom-agent]   classMajor." + className + "=<missing>");
                return;
            }
            try (DataInputStream in = new DataInputStream(jar.getInputStream(jar.getEntry(entryName)))) {
                int magic = in.readInt();
                if (magic != 0xCAFEBABE) {
                    System.out.println("[ebs-dom-agent]   classMajor." + className + "=<bad magic>");
                    return;
                }
                int minor = in.readUnsignedShort();
                int major = in.readUnsignedShort();
                System.out.println("[ebs-dom-agent]   classMajor." + className
                        + "=" + major + "." + minor);
            }
        } catch (IOException e) {
            System.out.println("[ebs-dom-agent]   classMajor." + className + "=<read failed: "
                    + e.getMessage() + ">");
        }
    }

    private static void printUsage() {
        System.err.println("Usage:");
        System.err.println("  java -cp ebs-dom-agent.jar[:<tools.jar>] \\");
        System.err.println("       com.pyebsdom.agent.AttachAgentByPid \\");
        System.err.println("       <pid> <agentJar> <agentArgs>");
        System.err.println();
        System.err.println("Arguments:");
        System.err.println("  pid       OS process ID of the target JVM");
        System.err.println("  agentJar  absolute path to ebs-dom-agent.jar");
        System.err.println("  agentArgs semicolon-separated key=value pairs");
        System.err.println("            Required: command=<name>;out=<path>");
        System.err.println();
        System.err.println("Supported commands (Phase 1):");
        System.err.println("  health    verify the agent can execute inside the target JVM");
        System.err.println();
        System.err.println("Examples:");
        System.err.println("  # Unix / Java 9+");
        System.err.println("  java --add-modules jdk.attach \\");
        System.err.println("       -cp ebs-dom-agent.jar \\");
        System.err.println("       com.pyebsdom.agent.AttachAgentByPid \\");
        System.err.println("       12345 /opt/pyebsdom/ebs-dom-agent.jar \\");
        System.err.println("       'command=health;out=/tmp/pyebsdom-health.json'");
        System.err.println();
        System.err.println("  # Windows / Java 8");
        System.err.println("  java -cp ebs-dom-agent.jar;%JAVA_HOME%\\lib\\tools.jar ^");
        System.err.println("       com.pyebsdom.agent.AttachAgentByPid ^");
        System.err.println("       12345 C:\\pyebsdom\\ebs-dom-agent.jar ^");
        System.err.println("       \"command=health;out=C:\\Temp\\pyebsdom-health.json\"");
    }
}
