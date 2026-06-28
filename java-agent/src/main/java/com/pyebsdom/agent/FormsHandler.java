package com.pyebsdom.agent;

import java.awt.Component;
import java.lang.reflect.Field;
import java.lang.reflect.Method;

/**
 * Reads the Oracle Forms <b>item handler id</b> from a live UI component.
 *
 * <p>
 * Every Oracle Forms UI item (VTextField, VButton, ExtendedCheckbox, …)
 * holds a private {@code mHandler} reference to its
 * {@code oracle.forms.handler.*Item} peer, which exposes a stable, item-native
 * {@code getHandlerId()} (the Forms runtime's item handle). The probe confirmed
 * this id is:
 * <ul>
 * <li><b>unique per item within a form module</b> (e.g. 1109, 1204, 1224…),
 * and</li>
 * <li><b>Forms-native</b> — assigned by the runtime, not the AWT creation
 * order, so it is far more stable than {@code Component.getName()}
 * ("VTextField69").</li>
 * </ul>
 *
 * <p>
 * Used both for extraction ({@link DomScanner} stamps {@code node.handlerId})
 * and for resolution ({@link ComponentResolver} matches a target by it). Pure
 * read-only reflection, fully guarded.
 */
public final class FormsHandler {

    private FormsHandler() {
    }

    /**
     * Returns the item handler id as a string, or {@code null} when the
     * component is not a Forms item or the id cannot be read.
     */
    public static String handlerId(Component comp) {
        if (comp == null)
            return null;
        try {
            Object handler = readFieldDeep(comp, "mHandler");
            if (handler == null)
                return null;

            // Preferred: public getHandlerId() (may be inherited from UICommon).
            try {
                Method m = handler.getClass().getMethod("getHandlerId");
                Object v = m.invoke(handler);
                if (v != null)
                    return String.valueOf(v);
            } catch (Throwable ignored) {
            }

            // Fallback: private mHandlerId field on the handler.
            Object idField = readFieldDeep(handler, "mHandlerId");
            if (idField != null)
                return String.valueOf(idField);
        } catch (Throwable ignored) {
        }
        return null;
    }

    /** Walk the class hierarchy to read a (possibly private) field by name. */
    private static Object readFieldDeep(Object target, String name) {
        for (Class<?> c = target.getClass(); c != null && c != Object.class; c = c.getSuperclass()) {
            try {
                Field f = c.getDeclaredField(name);
                f.setAccessible(true);
                return f.get(target);
            } catch (NoSuchFieldException ignored) {
                // try superclass
            } catch (Throwable t) {
                return null;
            }
        }
        return null;
    }
}
