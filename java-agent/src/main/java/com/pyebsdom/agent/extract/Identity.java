package com.pyebsdom.agent.extract;

import javax.accessibility.Accessible;
import javax.accessibility.AccessibleContext;
import java.awt.Component;
import java.awt.Dialog;
import java.awt.Frame;
import java.awt.Window;
import java.util.List;

/**
 * One home for component identity: canonical labels, scopes, and the
 * {@code <scope>::<label>::<ordinal>} semanticId — reconstructed from a
 * <b>live</b> {@link Component}.
 *
 * <p>Previously the replay resolver carried its own private copy of this logic
 * (its own comments called it "a mirror of IdentityResolver"). That copy is
 * gone; {@code execute.ComponentResolver} now delegates here, so scan-time and
 * replay-time identity share exactly one definition of "what is this widget's
 * canonical label and scope".
 *
 * <p>The pure-string normalisers ({@link #stripTabPagePrefix},
 * {@link #stripLevel}) are also the canonical versions; the scan-time
 * {@link IdentityResolver}, which works from {@link com.pyebsdom.agent.model.DomNode}
 * fields rather than live components, can route its string handling through
 * these as a follow-up de-duplication.
 */
public final class Identity {

    private Identity() { /* static utility */ }

    private static final String TAB_PAGE_MARKER = " tab page ";

    /**
     * Rebuilds the {@code <scope>::<label>::<ordinal>} identity for a live
     * component. The ordinal is best-effort here (always {@code 0}); when a
     * locator carries an explicit ordinal the resolver applies it instead.
     */
    public static String liveSemanticId(Component c, List<Component> all) {
        String scope = liveScopeId(c, all);
        String label = canonicalLabel(c);
        return scope + "::" + (label == null ? "" : label) + "::0";
    }

    /** Nearest stable container scope: {@code tab:<title>} or {@code form:<title>}. */
    public static String liveScopeId(Component c, List<Component> all) {
        Component cur = c.getParent();
        while (cur != null) {
            String scn = cur.getClass().getSimpleName();
            if ("TabPanelSheet".equals(scn)) {
                String an = accessibleName(cur);
                if (an != null) return "tab:" + an;
            }
            if (cur instanceof Window || "ExtendedFrame".equals(scn) || cur instanceof Dialog) {
                String t = windowTitle(cur);
                if (t != null) return "form:" + t;
            }
            cur = cur.getParent();
        }
        String an = accessibleName(c);
        if (an != null) {
            int idx = an.indexOf(TAB_PAGE_MARKER);
            if (idx > 0) return "tab:" + an.substring(0, idx).trim();
        }
        return "form:root";
    }

    /** Canonical, un-prefixed label from the live component's accessibility data. */
    public static String canonicalLabel(Component c) {
        try {
            if (c instanceof Accessible) {
                AccessibleContext ac = ((Accessible) c).getAccessibleContext();
                if (ac != null) {
                    String desc = ac.getAccessibleDescription();
                    if (notBlank(desc)) return desc.trim();
                    String name = ac.getAccessibleName();
                    if (notBlank(name)) return stripTabPagePrefix(name);
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    public static String accessibleName(Component comp) {
        try {
            if (comp instanceof Accessible) {
                AccessibleContext ac = ((Accessible) comp).getAccessibleContext();
                if (ac != null && ac.getAccessibleName() != null) return ac.getAccessibleName();
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    public static String windowTitle(Component c) {
        if (c instanceof Frame) return ((Frame) c).getTitle();
        if (c instanceof Dialog) return ((Dialog) c).getTitle();
        return accessibleName(c);
    }

    // ── Pure-string normalisers (shared canonical versions) ───────────────

    /** Strips a leading {@code "... tab page "} prefix from an accessible name. */
    public static String stripTabPagePrefix(String name) {
        if (name == null) return null;
        int idx = name.indexOf(TAB_PAGE_MARKER);
        return idx >= 0 ? name.substring(idx + TAB_PAGE_MARKER.length()).trim() : name.trim();
    }

    /** Strips an Oracle EWT {@code "Level N "} prefix from a tree-row label. */
    public static String stripLevel(String s) {
        if (s != null && s.startsWith("Level ")) {
            int sp = s.indexOf(' ', 6);
            if (sp > 0 && sp + 1 < s.length()) return s.substring(sp + 1).trim();
        }
        return s == null ? "" : s.trim();
    }

    private static boolean notBlank(String s) {
        return s != null && !s.trim().isEmpty();
    }
}
