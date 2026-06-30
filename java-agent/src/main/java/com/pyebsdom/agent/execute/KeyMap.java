package com.pyebsdom.agent.execute;

import java.awt.event.InputEvent;
import java.awt.event.KeyEvent;

/**
 * Resolves a human key name (e.g. {@code "TAB"}, {@code "F11"}, {@code "CTRL+S"})
 * into a {@link Stroke} — a modifier mask plus a virtual key code — suitable for
 * building {@link KeyEvent}s and dispatching them <b>directly to a component</b>.
 *
 * <p>This table was extracted from the old Robot wrapper. In the non-robotic
 * design it is used to synthesise targeted {@code KeyEvent}s via
 * {@code component.dispatchEvent(...)} rather than to drive global OS input, so
 * a key press goes to the intended widget and never to whatever window happens
 * to hold the real OS focus.
 */
public final class KeyMap {

    private KeyMap() { /* static utility */ }

    /** A resolved key press: an {@link InputEvent} modifier mask + a VK code. */
    public static final class Stroke {
        public final int modifiers;
        public final int keyCode;

        Stroke(int modifiers, int keyCode) {
            this.modifiers = modifiers;
            this.keyCode = keyCode;
        }
    }

    /**
     * Resolves a key name (case-insensitive). Supports the prefixes
     * {@code CTRL+}, {@code ALT+}, {@code SHIFT+} and the single-key names
     * below. Returns {@code null} when the name is not recognised.
     *
     * <pre>
     *   TAB ENTER RETURN ESC ESCAPE SPACE BACKSPACE DELETE INSERT
     *   HOME END PAGE_UP PAGE_DOWN UP DOWN LEFT RIGHT
     *   F1..F12   A..Z   0..9
     * </pre>
     */
    public static Stroke resolve(String keyName) {
        if (keyName == null) return null;
        String upper = keyName.trim().toUpperCase(java.util.Locale.ROOT);
        if (upper.isEmpty()) return null;

        if (upper.startsWith("CTRL+") && upper.length() > 5) {
            int kc = simpleKeyCode(upper.substring(5));
            return kc == -1 ? null : new Stroke(InputEvent.CTRL_DOWN_MASK, kc);
        }
        if (upper.startsWith("ALT+") && upper.length() > 4) {
            int kc = simpleKeyCode(upper.substring(4));
            return kc == -1 ? null : new Stroke(InputEvent.ALT_DOWN_MASK, kc);
        }
        if (upper.startsWith("SHIFT+") && upper.length() > 6) {
            int kc = simpleKeyCode(upper.substring(6));
            return kc == -1 ? null : new Stroke(InputEvent.SHIFT_DOWN_MASK, kc);
        }

        int kc = simpleKeyCode(upper);
        return kc == -1 ? null : new Stroke(0, kc);
    }

    /** Maps a single key name to its {@code VK_} code, or {@code -1}. */
    private static int simpleKeyCode(String upper) {
        switch (upper) {
            case "TAB":        return KeyEvent.VK_TAB;
            case "ENTER":
            case "RETURN":     return KeyEvent.VK_ENTER;
            case "ESC":
            case "ESCAPE":     return KeyEvent.VK_ESCAPE;
            case "SPACE":      return KeyEvent.VK_SPACE;
            case "BACKSPACE":
            case "BACK_SPACE": return KeyEvent.VK_BACK_SPACE;
            case "DELETE":
            case "DEL":        return KeyEvent.VK_DELETE;
            case "INSERT":     return KeyEvent.VK_INSERT;
            case "HOME":       return KeyEvent.VK_HOME;
            case "END":        return KeyEvent.VK_END;
            case "PAGE_UP":
            case "PAGEUP":     return KeyEvent.VK_PAGE_UP;
            case "PAGE_DOWN":
            case "PAGEDOWN":   return KeyEvent.VK_PAGE_DOWN;
            case "UP":         return KeyEvent.VK_UP;
            case "DOWN":       return KeyEvent.VK_DOWN;
            case "LEFT":       return KeyEvent.VK_LEFT;
            case "RIGHT":      return KeyEvent.VK_RIGHT;
            case "F1":         return KeyEvent.VK_F1;
            case "F2":         return KeyEvent.VK_F2;
            case "F3":         return KeyEvent.VK_F3;
            case "F4":         return KeyEvent.VK_F4;
            case "F5":         return KeyEvent.VK_F5;
            case "F6":         return KeyEvent.VK_F6;
            case "F7":         return KeyEvent.VK_F7;
            case "F8":         return KeyEvent.VK_F8;
            case "F9":         return KeyEvent.VK_F9;
            case "F10":        return KeyEvent.VK_F10;
            case "F11":        return KeyEvent.VK_F11;
            case "F12":        return KeyEvent.VK_F12;
            default:
                if (upper.length() == 1) {
                    char c = upper.charAt(0);
                    if (c >= 'A' && c <= 'Z') return KeyEvent.VK_A + (c - 'A');
                    if (c >= '0' && c <= '9') return KeyEvent.VK_0 + (c - '0');
                }
                return -1;
        }
    }
}
