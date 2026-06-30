/*
 * ============================================================================
 *  REFERENCE-ONLY BACKUP — NOT COMPILED, NOT WIRED INTO ANY CODE PATH.
 * ----------------------------------------------------------------------------
 *  This is the original java.awt.Robot wrapper (formerly SafeRobot), kept as a
 *  copy of the robotic mouse/keyboard implementation for the actions that are
 *  now performed non-robotically by execute.ModelActions.
 *
 *  It lives under backup-reference/ (outside src/main/java) precisely so the
 *  build cannot include it and nothing can invoke it automatically. There is
 *  NO runtime fallback to Robot. To consult or revive a technique, copy the
 *  relevant method into a new caller deliberately.
 * ============================================================================
 */
package com.pyebsdom.agent.execute;

import java.awt.*;
import java.awt.event.KeyEvent;
import java.awt.image.BufferedImage;

/**
 * Thin safety wrapper around {@link java.awt.Robot}.
 *
 * <h3>Thread safety</h3>
 * {@code Robot} itself is thread-safe.  All methods here are safe to call
 * from any thread, including after {@code SwingUtilities.invokeAndWait}
 * returns.
 *
 * <h3>Delays</h3>
 * A short auto-delay is injected between every Robot event to give the
 * target JVM time to process them.  The default is {@value #DEFAULT_DELAY_MS}
 * ms.  Callers can request additional explicit delays with {@link #delay(int)}.
 */
public final class RobotFallback {

    static final int DEFAULT_DELAY_MS     = 30;
    static final int TYPE_CHAR_DELAY_MS   = 20;
    static final int POST_ACTION_DELAY_MS = 80;

    private final Robot robot;

    // ── Construction ──────────────────────────────────────────────────────

    /**
     * Creates a {@code RobotFallback} using the default screen device.
     *
     * @throws AWTException       if the platform configuration does not allow
     *                            low-level input control
     * @throws HeadlessException  if the environment has no display
     */
    public RobotFallback() throws AWTException {
        com.pyebsdom.agent.runtime.AwtContext.ensureAppContext();
        robot = new Robot();
        robot.setAutoDelay(DEFAULT_DELAY_MS);
        robot.setAutoWaitForIdle(false);
    }

    // ── Mouse ─────────────────────────────────────────────────────────────

    /** Move the mouse to absolute screen coordinates. */
    public void moveMouse(int x, int y) {
        robot.mouseMove(x, y);
    }

    /**
     * Left-click at absolute screen coordinates.
     * Moves to the target first, then press + release.
     */
    public void click(int x, int y) {
        robot.mouseMove(x, y);
        robot.delay(DEFAULT_DELAY_MS);
        robot.mousePress(java.awt.event.InputEvent.BUTTON1_DOWN_MASK);
        robot.mouseRelease(java.awt.event.InputEvent.BUTTON1_DOWN_MASK);
    }

    /**
     * Double-click at absolute screen coordinates.
     */
    public void doubleClick(int x, int y) {
        click(x, y);
        robot.delay(DEFAULT_DELAY_MS);
        robot.mousePress(java.awt.event.InputEvent.BUTTON1_DOWN_MASK);
        robot.mouseRelease(java.awt.event.InputEvent.BUTTON1_DOWN_MASK);
    }

    // ── Keyboard ──────────────────────────────────────────────────────────

    /** Press and release a single key by key code. */
    public void pressKey(int keyCode) {
        robot.keyPress(keyCode);
        robot.keyRelease(keyCode);
    }

    /**
     * Press a modifier+key combination.
     * The modifier is held while the key is pressed, then released.
     *
     * @param modifierCode e.g. {@link KeyEvent#VK_CONTROL}
     * @param keyCode      e.g. {@link KeyEvent#VK_A}
     */
    public void pressCombo(int modifierCode, int keyCode) {
        robot.keyPress(modifierCode);
        robot.keyPress(keyCode);
        robot.keyRelease(keyCode);
        robot.keyRelease(modifierCode);
    }

    /**
     * Type a string character by character.
     *
     * <p>For each character the method determines whether a SHIFT modifier
     * is needed (upper-case letters, many punctuation characters).  Only
     * printable ASCII (0x20–0x7E) is supported reliably.  Characters outside
     * this range are silently skipped with a warning printed to stderr.
     *
     * @param text the text to type
     */
    public void typeText(String text) {
        if (text == null || text.isEmpty()) return;

        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            typeChar(ch);
            robot.delay(TYPE_CHAR_DELAY_MS);
        }
    }

    /**
     * Type a single character, handling shift for upper-case and symbols.
     */
    private void typeChar(char ch) {
        if (ch >= 'a' && ch <= 'z') {
            // lower-case letters: direct key code
            robot.keyPress(Character.toUpperCase(ch));
            robot.keyRelease(Character.toUpperCase(ch));
        } else if (ch >= 'A' && ch <= 'Z') {
            robot.keyPress(KeyEvent.VK_SHIFT);
            robot.keyPress(ch);
            robot.keyRelease(ch);
            robot.keyRelease(KeyEvent.VK_SHIFT);
        } else if (ch >= '0' && ch <= '9') {
            robot.keyPress(ch);
            robot.keyRelease(ch);
        } else if (ch == ' ') {
            robot.keyPress(KeyEvent.VK_SPACE);
            robot.keyRelease(KeyEvent.VK_SPACE);
        } else if (ch == '\t') {
            robot.keyPress(KeyEvent.VK_TAB);
            robot.keyRelease(KeyEvent.VK_TAB);
        } else if (ch == '\n') {
            robot.keyPress(KeyEvent.VK_ENTER);
            robot.keyRelease(KeyEvent.VK_ENTER);
        } else {
            // Use a lookup table for common ASCII symbols
            Integer[] codes = symbolKeyCode(ch);
            if (codes != null) {
                if (codes.length == 2) {
                    // codes[0] = modifier, codes[1] = key
                    robot.keyPress(codes[0]);
                    robot.keyPress(codes[1]);
                    robot.keyRelease(codes[1]);
                    robot.keyRelease(codes[0]);
                } else {
                    robot.keyPress(codes[0]);
                    robot.keyRelease(codes[0]);
                }
            } else {
                System.err.println("[RobotFallback] unsupported char: '" + ch
                        + "' (U+" + Integer.toHexString(ch) + "), skipping");
            }
        }
    }

    /**
     * Returns {modifier, key} or {key} for common ASCII punctuation on a
     * US keyboard layout.  Returns {@code null} for unmapped characters.
     */
    private static Integer[] symbolKeyCode(char ch) {
        switch (ch) {
            // No modifier needed
            case '-': return new Integer[]{ KeyEvent.VK_MINUS };
            case '=': return new Integer[]{ KeyEvent.VK_EQUALS };
            case '[': return new Integer[]{ KeyEvent.VK_OPEN_BRACKET };
            case ']': return new Integer[]{ KeyEvent.VK_CLOSE_BRACKET };
            case '\\': return new Integer[]{ KeyEvent.VK_BACK_SLASH };
            case ';': return new Integer[]{ KeyEvent.VK_SEMICOLON };
            case '\'': return new Integer[]{ KeyEvent.VK_QUOTE };
            case ',': return new Integer[]{ KeyEvent.VK_COMMA };
            case '.': return new Integer[]{ KeyEvent.VK_PERIOD };
            case '/': return new Integer[]{ KeyEvent.VK_SLASH };
            case '`': return new Integer[]{ KeyEvent.VK_BACK_QUOTE };
            // Shift-needed
            case '!': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_1 };
            case '@': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_2 };
            case '#': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_3 };
            case '$': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_4 };
            case '%': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_5 };
            case '^': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_6 };
            case '&': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_7 };
            case '*': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_8 };
            case '(': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_9 };
            case ')': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_0 };
            case '_': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_MINUS };
            case '+': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_EQUALS };
            case '{': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_OPEN_BRACKET };
            case '}': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_CLOSE_BRACKET };
            case '|': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_BACK_SLASH };
            case ':': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_SEMICOLON };
            case '"': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_QUOTE };
            case '<': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_COMMA };
            case '>': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_PERIOD };
            case '?': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_SLASH };
            case '~': return new Integer[]{ KeyEvent.VK_SHIFT, KeyEvent.VK_BACK_QUOTE };
            default:  return null;
        }
    }

    // ── Named key resolution ──────────────────────────────────────────────

    /**
     * Resolve a named key string (case-insensitive) to a sequence of Robot
     * press actions.  Returns {@code true} if the key was recognised and
     * pressed, {@code false} otherwise.
     *
     * <p>Supported names:
     * <pre>
     *   TAB ENTER RETURN ESC ESCAPE SPACE
     *   F1 F2 F3 F4 F5 F6 F7 F8 F9 F10 F11 F12
     *   UP DOWN LEFT RIGHT HOME END PAGE_UP PAGE_DOWN
     *   DELETE DEL BACKSPACE BACK_SPACE INSERT
     *   CTRL+S CTRL+F CTRL+A CTRL+C CTRL+V CTRL+X CTRL+Z CTRL+Y
     *   ALT+F4
     * </pre>
     *
     * @param keyName the name string from the agent command
     * @return {@code true} if the key/combination was executed
     */
    public boolean pressNamedKey(String keyName) {
        if (keyName == null || keyName.trim().isEmpty()) return false;
        String upper = keyName.trim().toUpperCase();

        // Combos: CTRL+x, ALT+x
        if (upper.startsWith("CTRL+") && upper.length() > 5) {
            String rest = upper.substring(5);
            int keyCode = simpleKeyCode(rest);
            if (keyCode != -1) {
                pressCombo(KeyEvent.VK_CONTROL, keyCode);
                return true;
            }
        }
        if (upper.startsWith("ALT+") && upper.length() > 4) {
            String rest = upper.substring(4);
            int keyCode = simpleKeyCode(rest);
            if (keyCode != -1) {
                pressCombo(KeyEvent.VK_ALT, keyCode);
                return true;
            }
        }
        if (upper.startsWith("SHIFT+") && upper.length() > 6) {
            String rest = upper.substring(6);
            int keyCode = simpleKeyCode(rest);
            if (keyCode != -1) {
                pressCombo(KeyEvent.VK_SHIFT, keyCode);
                return true;
            }
        }

        // Single keys
        int code = simpleKeyCode(upper);
        if (code != -1) {
            pressKey(code);
            return true;
        }
        return false;
    }

    /** Map a single key name to its VK_ code, or -1. */
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
            // Single letter/digit (for combos like CTRL+S)
            case "A": return KeyEvent.VK_A;
            case "B": return KeyEvent.VK_B;
            case "C": return KeyEvent.VK_C;
            case "D": return KeyEvent.VK_D;
            case "E": return KeyEvent.VK_E;
            case "F": return KeyEvent.VK_F;
            case "G": return KeyEvent.VK_G;
            case "H": return KeyEvent.VK_H;
            case "I": return KeyEvent.VK_I;
            case "J": return KeyEvent.VK_J;
            case "K": return KeyEvent.VK_K;
            case "L": return KeyEvent.VK_L;
            case "M": return KeyEvent.VK_M;
            case "N": return KeyEvent.VK_N;
            case "O": return KeyEvent.VK_O;
            case "P": return KeyEvent.VK_P;
            case "Q": return KeyEvent.VK_Q;
            case "R": return KeyEvent.VK_R;
            case "S": return KeyEvent.VK_S;
            case "T": return KeyEvent.VK_T;
            case "U": return KeyEvent.VK_U;
            case "V": return KeyEvent.VK_V;
            case "W": return KeyEvent.VK_W;
            case "X": return KeyEvent.VK_X;
            case "Y": return KeyEvent.VK_Y;
            case "Z": return KeyEvent.VK_Z;
            case "0": return KeyEvent.VK_0;
            case "1": return KeyEvent.VK_1;
            case "2": return KeyEvent.VK_2;
            case "3": return KeyEvent.VK_3;
            case "4": return KeyEvent.VK_4;
            case "5": return KeyEvent.VK_5;
            case "6": return KeyEvent.VK_6;
            case "7": return KeyEvent.VK_7;
            case "8": return KeyEvent.VK_8;
            case "9": return KeyEvent.VK_9;
            default:  return -1;
        }
    }

    // ── Screenshot ────────────────────────────────────────────────────────

    /**
     * Capture the entire primary screen.
     *
     * @return {@link BufferedImage} of the full screen
     */
    public BufferedImage captureFullScreen() {
        Dimension screen = Toolkit.getDefaultToolkit().getScreenSize();
        return robot.createScreenCapture(new Rectangle(0, 0, screen.width, screen.height));
    }

    /**
     * Capture a specific region of the screen.
     *
     * @param rect screen-coordinate rectangle to capture
     * @return {@link BufferedImage} of the specified region
     */
    public BufferedImage captureRegion(Rectangle rect) {
        // Clamp to screen dimensions to avoid out-of-bounds
        Dimension screen = Toolkit.getDefaultToolkit().getScreenSize();
        int x = Math.max(0, rect.x);
        int y = Math.max(0, rect.y);
        int w = Math.min(rect.width,  screen.width  - x);
        int h = Math.min(rect.height, screen.height - y);
        if (w <= 0 || h <= 0) {
            return captureFullScreen();
        }
        return robot.createScreenCapture(new Rectangle(x, y, w, h));
    }

    // ── Utility ───────────────────────────────────────────────────────────

    /**
     * Block for {@code ms} milliseconds (delegates to {@link Robot#delay}).
     */
    public void delay(int ms) {
        if (ms > 0) robot.delay(ms);
    }

    /**
     * Wait for the AWT event queue to finish processing pending events.
     * Calls {@link Robot#waitForIdle()}.
     */
    public void waitForIdle() {
        robot.waitForIdle();
    }
}
