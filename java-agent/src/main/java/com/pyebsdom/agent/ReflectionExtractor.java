package com.pyebsdom.agent;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.LinkedHashMap;
import java.util.Set;
import java.util.List;

/**
 * Safe, allowlist-based reflection extractor for AWT/Swing components.
 *
 * <p>Only zero-argument, public, non-static methods whose names are on the
 * explicit allowlist are called.  Every invocation is wrapped in a
 * try-catch so a broken component cannot propagate exceptions up to the
 * scanner.
 *
 * <p>Return values are converted to strings with {@link Object#toString()}.
 * Null returns are stored as the literal string {@code "null"}.
 * Results that are arrays are rendered as a comma-separated list.
 *
 * <h3>Why an allowlist?</h3>
 * The target JVM contains live EBS business logic.  Calling arbitrary
 * methods (e.g. {@code dispose()}, {@code doClick()}, {@code delete()})
 * would mutate application state or crash the forms session.  The allowlist
 * is intentionally limited to <em>read-only</em>, <em>state-reporting</em>
 * methods.
 */
public final class ReflectionExtractor {

    /** Ordered set of safe zero-argument method names to call. */
    private static final Set<String> ALLOWLIST = Collections.unmodifiableSet(
            new LinkedHashSet<>(Arrays.asList(
                    "getText",
                    "getName",
                    "getLabel",
                    "getValue",
                    "getToolTipText",
                    "getTitle",
                    "getSelectedItem",
                    "getSelectedValue",
                    "getSelectedIndex",
                    "getItemCount",
                    "getRowCount",
                    "getColumnCount",
                    "isVisible",
                    "isShowing",
                    "isEnabled",
                    "isFocusable",
                    "isFocusOwner",
                    "isEditable",
                    "isSelected",
                    "isChecked"
            ))
    );

    private ReflectionExtractor() {}

    /**
     * Invokes all allowlisted methods found on {@code obj} and returns the
     * results as a name → stringified-value map.
     *
     * <p>Methods that are not present on the target class are silently
     * skipped.  Methods that throw exceptions during invocation are skipped
     * and the exception is not propagated.
     *
     * @param obj any object, typically an AWT/Swing component
     * @return ordered map of method name → string result
     */
    public static Map<String, String> extract(Object obj) {
        if (obj == null) return Collections.emptyMap();

        Map<String, String> result = new LinkedHashMap<>();
        Class<?> clazz = obj.getClass();

        for (String methodName : ALLOWLIST) {
            try {
                Method m = resolveMethod(clazz, methodName);
                if (m == null) continue;

                Object returnValue = m.invoke(obj);
                result.put(methodName, stringify(returnValue));

            } catch (Exception ignored) {
                // Skip silently — the component may not support this method
                // or may throw for legitimate reasons (e.g. not initialised).
            }
        }

        return result;
    }

    /**
     * Safely extracts visible/selectable option values from components that
     * expose read-only indexed item APIs (Swing JComboBox/JList and many Oracle
     * EWT poplists). Returns an empty list when no such API exists.
     */
    public static List<String> extractOptions(Object obj, int limit) {
        if (obj == null || limit <= 0) return Collections.emptyList();

        List<String> direct = extractIndexedOptions(obj, "getItemCount", "getItemAt", limit);
        if (!direct.isEmpty()) return direct;

        try {
            Method getModel = obj.getClass().getMethod("getModel");
            Object model = getModel.invoke(obj);
            if (model != null) {
                List<String> modelOptions = extractIndexedOptions(model, "getSize", "getElementAt", limit);
                if (!modelOptions.isEmpty()) return modelOptions;
            }
        } catch (Exception ignored) {}

        return Collections.emptyList();
    }

    // ── Helpers ───────────────────────────────────────────────────────────

    /**
     * Resolves a public, zero-argument method by name, searching the entire
     * class hierarchy including interfaces.  Returns {@code null} if not found.
     */
    private static Method resolveMethod(Class<?> clazz, String name) {
        // Walk up the hierarchy; getDeclaredMethods misses inherited methods,
        // getMethods only sees public methods — exactly what we want here.
        try {
            return clazz.getMethod(name);        // public + zero-arg
        } catch (NoSuchMethodException e) {
            return null;
        }
    }

    private static List<String> extractIndexedOptions(
            Object obj,
            String countMethodName,
            String itemMethodName,
            int limit) {
        List<String> values = new ArrayList<>();
        try {
            Method countMethod = obj.getClass().getMethod(countMethodName);
            Object countObj = countMethod.invoke(obj);
            int count = toInt(countObj);
            if (count <= 0) return values;

            Method itemMethod = resolveIntMethod(obj.getClass(), itemMethodName);
            if (itemMethod == null) return values;

            int n = Math.min(count, limit);
            for (int i = 0; i < n; i++) {
                try {
                    Object item = itemMethod.invoke(obj, i);
                    values.add(item == null ? "" : item.toString());
                } catch (Exception ignored) {}
            }
        } catch (Exception ignored) {}
        return values;
    }

    private static Method resolveIntMethod(Class<?> clazz, String name) {
        try {
            return clazz.getMethod(name, int.class);
        } catch (NoSuchMethodException ignored) {}
        try {
            return clazz.getMethod(name, Integer.TYPE);
        } catch (NoSuchMethodException ignored) {}
        return null;
    }

    private static int toInt(Object value) {
        if (value instanceof Number) return ((Number) value).intValue();
        if (value == null) return -1;
        try {
            return Integer.parseInt(value.toString());
        } catch (NumberFormatException ignored) {
            return -1;
        }
    }

    /** Converts a method return value to a safe display string. */
    private static String stringify(Object value) {
        if (value == null) return "null";
        if (value.getClass().isArray()) {
            return arrayToString(value);
        }
        return value.toString();
    }

    private static String arrayToString(Object array) {
        if (array instanceof Object[]) {
            Object[] arr = (Object[]) array;
            StringBuilder sb = new StringBuilder("[");
            for (int i = 0; i < arr.length; i++) {
                if (i > 0) sb.append(", ");
                sb.append(arr[i] == null ? "null" : arr[i].toString());
            }
            sb.append("]");
            return sb.toString();
        }
        // Primitive arrays — use java.util.Arrays
        if (array instanceof int[])     return Arrays.toString((int[])     array);
        if (array instanceof long[])    return Arrays.toString((long[])    array);
        if (array instanceof double[])  return Arrays.toString((double[])  array);
        if (array instanceof boolean[]) return Arrays.toString((boolean[]) array);
        return array.toString();
    }
}
