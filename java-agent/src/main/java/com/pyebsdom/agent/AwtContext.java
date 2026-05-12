package com.pyebsdom.agent;

import java.awt.Window;
import java.lang.ref.Reference;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * AWT helpers that work from a Java Attach thread.
 *
 * Java Web Start often runs with multiple sun.awt.AppContext instances. The
 * attach thread is not part of any of those contexts, so APIs like
 * Window.getWindows() can throw NullPointerException because
 * AppContext.getAppContext() returns null. This helper reflects over all live
 * AppContexts and reads each context's Window.class list directly.
 */
final class AwtContext {

    private AwtContext() {}

    static void ensureAppContext() {
        try {
            Class<?> appContextClass = Class.forName("sun.awt.AppContext");
            Method getAppContext = appContextClass.getMethod("getAppContext");
            Object context = null;
            try {
                context = getAppContext.invoke(null);
            } catch (Throwable ignored) {
                // Some attach-thread calls fail before returning null.
            }
            if (context != null) {
                return;
            }

            Class<?> sunToolkitClass = Class.forName("sun.awt.SunToolkit");
            Method createNewAppContext = sunToolkitClass.getDeclaredMethod("createNewAppContext");
            createNewAppContext.setAccessible(true);
            createNewAppContext.invoke(null);
        } catch (Throwable failure) {
            System.err.println("[ebs-dom-agent] Could not initialise AWT AppContext: "
                    + failure.getClass().getName() + ": " + failure.getMessage());
        }
    }

    static Window[] getWindows() {
        List<Window> windows = new ArrayList<Window>();

        try {
            Class<?> appContextClass = Class.forName("sun.awt.AppContext");
            Method getAppContexts = appContextClass.getMethod("getAppContexts");
            Method get = appContextClass.getMethod("get", Object.class);

            @SuppressWarnings("unchecked")
            Set<Object> contexts = (Set<Object>) getAppContexts.invoke(null);
            for (Object context : contexts) {
                Object value = get.invoke(context, Window.class);
                if (value instanceof Iterable) {
                    for (Object item : (Iterable<?>) value) {
                        Window window = dereferenceWindow(item);
                        if (window != null && !windows.contains(window)) {
                            windows.add(window);
                        }
                    }
                }
            }
        } catch (Throwable reflectionFailure) {
            System.err.println("[ebs-dom-agent] AppContext window enumeration failed: "
                    + reflectionFailure.getClass().getName() + ": "
                    + reflectionFailure.getMessage());
        }

        if (!windows.isEmpty()) {
            return windows.toArray(new Window[windows.size()]);
        }

        try {
            return Window.getWindows();
        } catch (Throwable fallbackFailure) {
            System.err.println("[ebs-dom-agent] Window.getWindows fallback failed: "
                    + fallbackFailure.getClass().getName() + ": "
                    + fallbackFailure.getMessage());
            return new Window[0];
        }
    }

    private static Window dereferenceWindow(Object item) throws Exception {
        Object value = item;
        if (value instanceof Reference) {
            value = ((Reference<?>) value).get();
        }
        if (value instanceof Window) {
            return (Window) value;
        }

        // Some JDK internals use specialized weak-reference classes; try a
        // public get() method before giving up.
        if (value != null) {
            try {
                Method get = value.getClass().getMethod("get");
                Object target = get.invoke(value);
                if (target instanceof Window) {
                    return (Window) target;
                }
            } catch (NoSuchMethodException ignored) {
                // Fall through.
            }

            // Last chance: a private referent field on Reference subclasses.
            try {
                Field referent = Reference.class.getDeclaredField("referent");
                referent.setAccessible(true);
                Object target = referent.get(value);
                if (target instanceof Window) {
                    return (Window) target;
                }
            } catch (Throwable ignored) {
                // Fall through.
            }
        }
        return null;
    }
}
