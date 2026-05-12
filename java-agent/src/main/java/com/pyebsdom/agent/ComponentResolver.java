package com.pyebsdom.agent;

import javax.accessibility.Accessible;
import javax.accessibility.AccessibleContext;
import javax.swing.*;
import java.awt.*;
import java.util.ArrayList;
import java.util.List;

/**
 * Resolves a live AWT/Swing {@link Component} from the parameters of an
 * agent command, using up to eight progressively less specific strategies.
 *
 * <h3>Resolution strategies (in priority order)</h3>
 * <ol>
 *   <li><b>locatorPath</b>       — exact match on DOM path
 *       ({@code JFrame[0]/JPanel[0]/JTextField[2]})</li>
 *   <li><b>locatorName</b>       — exact match on {@code Component.getName()}</li>
 *   <li><b>locatorAccessibleName</b> — exact match on accessible name</li>
 *   <li><b>locatorText</b>       — exact match on visible text</li>
 *   <li><b>locatorClassName + locatorName</b> — class simple-name prefix +
 *       component name</li>
 *   <li><b>locatorClassName + locatorAccessibleName</b></li>
 *   <li><b>contains fallback</b> — case-insensitive substring in name,
 *       accessible name or text; first match wins</li>
 *   <li><b>bounds fallback</b>   — if {@code locatorBounds=x,y,w,h} is
 *       supplied, the component whose screen rectangle best overlaps</li>
 * </ol>
 *
 * <h3>Thread safety</h3>
 * {@link #resolve(AgentCommand)} <em>must</em> be called on the AWT Event
 * Dispatch Thread.  All Window/component traversal reads component state
 * directly.
 */
public final class ComponentResolver {

    private ComponentResolver() {}

    // ── Public API ────────────────────────────────────────────────────────

    /**
     * Attempt to resolve a component from the command's locator parameters.
     *
     * <p>Must be called on the EDT.
     *
     * @param cmd the agent command carrying locator keys
     * @return the first matching {@link Component}, or {@code null} if none found
     */
    public static Component resolve(AgentCommand cmd) {
        String locPath    = cmd.getParam("locatorpath");     // lower-cased by parser
        String locName    = cmd.getParam("locatorname");
        String locAccName = cmd.getParam("locatoraccessiblename");
        String locText    = cmd.getParam("locatortext");
        String locClass   = cmd.getParam("locatorclassname");
        String locBounds  = cmd.getParam("locatorbounds");

        List<Component> all = collectAllVisible();

        // Strategy 1: exact path
        if (notBlank(locPath)) {
            for (Component c : all) {
                if (locPath.equals(componentPath(c))) return c;
            }
        }

        // Strategy 2: exact name
        if (notBlank(locName)) {
            for (Component c : all) {
                if (locName.equals(c.getName())) return c;
            }
        }

        // Strategy 3: exact accessible name
        if (notBlank(locAccName)) {
            for (Component c : all) {
                if (locAccName.equals(accessibleName(c))) return c;
            }
        }

        // Strategy 4: exact text
        if (notBlank(locText)) {
            for (Component c : all) {
                if (locText.equals(componentText(c))) return c;
            }
        }

        // Strategy 5: class + name
        if (notBlank(locClass) && notBlank(locName)) {
            for (Component c : all) {
                if (classMatch(c, locClass) && locName.equals(c.getName())) return c;
            }
        }

        // Strategy 6: class + accessible name
        if (notBlank(locClass) && notBlank(locAccName)) {
            for (Component c : all) {
                if (classMatch(c, locClass) && locAccName.equals(accessibleName(c))) return c;
            }
        }

        // Strategy 7: contains fallback (first locator that is non-blank)
        String contains = firstNonBlank(locPath, locName, locAccName, locText);
        if (notBlank(contains)) {
            String lower = contains.toLowerCase(java.util.Locale.ROOT);
            for (Component c : all) {
                if (containsIgnoreCase(c.getName(), lower)
                        || containsIgnoreCase(accessibleName(c), lower)
                        || containsIgnoreCase(componentText(c), lower)) {
                    return c;
                }
            }
        }

        // Strategy 8: bounds fallback
        if (notBlank(locBounds)) {
            int[] bb = parseBounds(locBounds);
            if (bb != null) {
                return findByBounds(all, bb[0], bb[1], bb[2], bb[3]);
            }
        }

        return null;
    }

    /**
     * Build a JSON snippet describing a resolved component for use in
     * action result envelopes.
     */
    public static String componentJson(Component comp) {
        if (comp == null) return "null";
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"className\":").append(JsonUtil.quoted(comp.getClass().getName())).append(',');
        sb.append("\"simpleName\":").append(JsonUtil.quoted(comp.getClass().getSimpleName())).append(',');
        sb.append("\"name\":").append(JsonUtil.quoted(comp.getName())).append(',');
        sb.append("\"accessibleName\":").append(JsonUtil.quoted(accessibleName(comp))).append(',');
        sb.append("\"text\":").append(JsonUtil.quoted(componentText(comp))).append(',');

        Rectangle b = comp.getBounds();
        sb.append("\"bounds\":{")
          .append("\"x\":").append(b.x).append(',')
          .append("\"y\":").append(b.y).append(',')
          .append("\"width\":").append(b.width).append(',')
          .append("\"height\":").append(b.height)
          .append("},");

        int sx = -1, sy = -1;
        try {
            if (comp.isShowing()) {
                Point p = comp.getLocationOnScreen();
                sx = p.x; sy = p.y;
            }
        } catch (Exception ignored) {}
        sb.append("\"screenX\":").append(sx).append(',');
        sb.append("\"screenY\":").append(sy);
        sb.append('}');
        return sb.toString();
    }

    // ── Component traversal ───────────────────────────────────────────────

    /** Collect all visible components across all visible windows. */
    private static List<Component> collectAllVisible() {
        List<Component> result = new ArrayList<>();
        for (Window w : AwtContext.getWindows()) {
            if (w.isVisible()) collectComponents(w, result);
        }
        return result;
    }

    private static void collectComponents(Component comp, List<Component> out) {
        if (!comp.isVisible()) return;
        out.add(comp);
        if (comp instanceof Container) {
            for (Component child : ((Container) comp).getComponents()) {
                collectComponents(child, out);
            }
        }
    }

    // ── Path reconstruction ───────────────────────────────────────────────

    /**
     * Reconstruct the DOM path for a live component by walking up the
     * parent chain.  The format matches {@link DomScanner}:
     * {@code JFrame[0]/JPanel[1]/JTextField[2]}.
     */
    public static String componentPath(Component comp) {
        if (comp == null) return "";
        List<String> segments = new ArrayList<>();
        Component current = comp;
        while (current != null) {
            Container parent = current.getParent();
            int index = 0;
            if (parent != null) {
                Component[] siblings = parent.getComponents();
                for (int i = 0; i < siblings.length; i++) {
                    if (siblings[i] == current) { index = i; break; }
                }
            }
            segments.add(0, current.getClass().getSimpleName() + "[" + index + "]");
            if (current instanceof Window) break;
            current = parent;
        }
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < segments.size(); i++) {
            if (i > 0) sb.append('/');
            sb.append(segments.get(i));
        }
        return sb.toString();
    }

    // ── Helper extractors ─────────────────────────────────────────────────

    private static String accessibleName(Component comp) {
        try {
            if (comp instanceof Accessible) {
                AccessibleContext ac = ((Accessible) comp).getAccessibleContext();
                if (ac != null && ac.getAccessibleName() != null) {
                    return ac.getAccessibleName();
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    private static String componentText(Component comp) {
        for (String method : new String[] { "getText", "getTitle" }) {
            try {
                Object val = comp.getClass().getMethod(method).invoke(comp);
                if (val != null) return val.toString();
            } catch (Exception ignored) {}
        }
        return null;
    }

    private static boolean classMatch(Component comp, String locClass) {
        String name = comp.getClass().getSimpleName();
        String fqn  = comp.getClass().getName();
        return name.equalsIgnoreCase(locClass)
            || fqn.equalsIgnoreCase(locClass)
            || name.toLowerCase(java.util.Locale.ROOT)
                   .contains(locClass.toLowerCase(java.util.Locale.ROOT));
    }

    private static boolean containsIgnoreCase(String target, String lowerSubstring) {
        if (target == null || target.isEmpty()) return false;
        return target.toLowerCase(java.util.Locale.ROOT).contains(lowerSubstring);
    }

    // ── Bounds fallback ───────────────────────────────────────────────────

    private static Component findByBounds(List<Component> all,
                                           int x, int y, int w, int h) {
        Rectangle target = new Rectangle(x, y, w, h);
        Component best = null;
        int bestOverlap = 0;
        for (Component c : all) {
            try {
                if (!c.isShowing()) continue;
                Point loc = c.getLocationOnScreen();
                Rectangle cr = new Rectangle(loc.x, loc.y, c.getWidth(), c.getHeight());
                Rectangle inter = cr.intersection(target);
                if (!inter.isEmpty()) {
                    int area = inter.width * inter.height;
                    if (area > bestOverlap) {
                        bestOverlap = area;
                        best = c;
                    }
                }
            } catch (Exception ignored) {}
        }
        return best;
    }

    // ── Utility ───────────────────────────────────────────────────────────

    private static boolean notBlank(String s) {
        return s != null && !s.trim().isEmpty();
    }

    private static String firstNonBlank(String... values) {
        for (String v : values) {
            if (notBlank(v)) return v;
        }
        return null;
    }

    private static int[] parseBounds(String boundsStr) {
        // Expected format: "x,y,w,h"
        try {
            String[] parts = boundsStr.split(",", 4);
            if (parts.length == 4) {
                return new int[] {
                    Integer.parseInt(parts[0].trim()),
                    Integer.parseInt(parts[1].trim()),
                    Integer.parseInt(parts[2].trim()),
                    Integer.parseInt(parts[3].trim())
                };
            }
        } catch (NumberFormatException ignored) {}
        return null;
    }
}
