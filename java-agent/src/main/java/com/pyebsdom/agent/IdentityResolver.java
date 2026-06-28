package com.pyebsdom.agent;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Post-scan pass that gives every node a <b>stable cross-scan identity</b> and
 * a
 * <b>verified-unique primary locator</b>, computed inside the JVM while the
 * live
 * tree is still observable.
 *
 * <h3>Problem this solves</h3>
 * The previous design had no stable identity: {@code eN} ids reset every scan,
 * {@code path} is index-based and churns on tab switches, {@code getName()}
 * ("VTextField431") is creation-order dependent, and {@code accessibleName}
 * collides massively (dozens of "To (LOV)", one "Order Number" per grid row).
 * The resolver then did first-match-wins, silently binding actions to the wrong
 * widget. This pass makes identity explicit and proves uniqueness.
 *
 * <h3>What it produces, per node</h3>
 * <ul>
 * <li>{@link DomNode#canonicalLabel} — the un-prefixed business label
 * (accessibleDescription preferred; tab-page prefix stripped from
 * accessibleName otherwise).</li>
 * <li>{@link DomNode#semanticId} —
 * {@code <scope>::<canonicalLabel>::<ordinal>},
 * stable as long as the scope + label are stable. Persist THIS, not eN.</li>
 * <li>{@link DomNode#primaryLocator} — the most specific locator confirmed to
 * resolve to exactly one node in this scan. Scope-qualified and
 * ordinal-qualified when needed so even grid cells are addressable.</li>
 * <li>{@link DomNode#locatorAmbiguous} — true when only an ordinal separates
 * this node from siblings (informational; the locator is still unique).</li>
 * </ul>
 *
 * <h3>Scope</h3>
 * A node's scope is its nearest ancestor that is itself a stable container:
 * a {@code TabPage} (ownerTab), a {@code Grid}, a window/{@code ExtendedFrame},
 * or a dialog. Scope-qualifying a label converts "not globally unique" into
 * "unique within this region", which is exactly how a human disambiguates.
 */
public final class IdentityResolver {

    private IdentityResolver() {
    }

    /** Run identity resolution over every window root in the scan. */
    public static void resolve(List<DomNode> windows) {
        if (windows == null)
            return;
        List<DomNode> all = new ArrayList<>();
        for (DomNode w : windows)
            flatten(w, all);

        // 1) Canonical labels + scopes first (other steps depend on them).
        Map<DomNode, DomNode> parent = buildParentMap(windows);
        for (DomNode n : all) {
            n.canonicalLabel = canonicalLabel(n);
        }

        // 2) Global frequency of (scopeId, strategy, value) so we can test
        // uniqueness in O(1) instead of re-walking the tree per node.
        Map<String, Integer> freq = new HashMap<>();
        for (DomNode n : all) {
            for (Candidate c : candidates(n, parent)) {
                freq.merge(c.key(), 1, Integer::sum);
            }
        }

        // 3) Pick each node's most specific verified-unique locator + semanticId.
        Map<String, Integer> ordinalCounter = new HashMap<>();
        for (DomNode n : all) {
            String scopeId = scopeId(n, parent);
            List<Candidate> cands = candidates(n, parent);

            Candidate chosen = null;
            for (Candidate c : cands) {
                if (freq.getOrDefault(c.key(), 0) == 1) {
                    chosen = c;
                    break;
                }
            }

            if (chosen != null) {
                n.primaryLocator = new LocatorCandidate(
                        chosen.strategy, chosen.value, 1.0, true, scopeId, n.recordIndex);
                n.locatorAmbiguous = false;
                n.semanticId = scopeId + "::" + safe(n.canonicalLabel) + "::0";
            } else {
                // Not unique by label alone — disambiguate by ordinal within
                // (scope, label). recordIndex (set by StructureAnnotator) is
                // preferred; otherwise assign a deterministic running ordinal.
                String okey = scopeId + "|" + safe(n.canonicalLabel);
                int ord = n.recordIndex >= 0
                        ? n.recordIndex
                        : ordinalCounter.merge(okey, 1, Integer::sum) - 1;
                Candidate base = cands.isEmpty() ? null : cands.get(0);
                String strat = base != null ? base.strategy : "accessibleName";
                String val = base != null ? base.value : safe(n.canonicalLabel);
                n.primaryLocator = new LocatorCandidate(
                        strat, val, 0.85, true, scopeId, ord);
                n.locatorAmbiguous = true;
                n.semanticId = scopeId + "::" + safe(n.canonicalLabel) + "::" + ord;
            }
        }
    }

    // ── Candidate locators, most-specific first ───────────────────────────

    private static List<Candidate> candidates(DomNode n, Map<DomNode, DomNode> parent) {
        String scope = scopeId(n, parent);
        List<Candidate> out = new ArrayList<>();
        // treePath is already a unique path within a tree.
        if (notBlank(n.treePath)) {
            out.add(new Candidate("treePath", n.treePath, scope));
        }
        // canonicalLabel scoped to the region is the primary business locator.
        if (notBlank(n.canonicalLabel)) {
            out.add(new Candidate("canonicalLabel", n.canonicalLabel, scope));
        }
        if (notBlank(n.accessibleName)) {
            out.add(new Candidate("accessibleName", n.accessibleName, scope));
        }
        if (notBlank(n.name) && !n.name.startsWith("null")) {
            out.add(new Candidate("name", n.name, scope));
        }
        if (notBlank(n.text)) {
            out.add(new Candidate("text", n.text, scope));
        }
        return out;
    }

    // ── Canonical label ───────────────────────────────────────────────────

    /**
     * The business label without the volatile "&lt;Tab&gt; tab page " prefix.
     * Oracle Forms exposes the un-prefixed label as accessibleDescription, so
     * prefer it; otherwise strip the prefix from accessibleName.
     */
    static String canonicalLabel(DomNode n) {
        if (notBlank(n.accessibleDescription))
            return n.accessibleDescription.trim();
        String s = n.accessibleName;
        if (notBlank(s)) {
            int idx = s.indexOf(" tab page ");
            if (idx >= 0 && idx + 10 < s.length()) {
                return s.substring(idx + 10).trim();
            }
            return s.trim();
        }
        if (notBlank(n.displayName))
            return n.displayName.trim();
        if (notBlank(n.text))
            return n.text.trim();
        return notBlank(n.simpleClassName) ? n.simpleClassName : "";
    }

    // ── Scope identity ────────────────────────────────────────────────────

    /**
     * The semanticId-style key of the nearest stable container: a grid, a tab
     * page (ownerTab), a dialog, or the form/window. Anchors a label so it is
     * unique within its region rather than globally.
     */
    private static String scopeId(DomNode n, Map<DomNode, DomNode> parent) {
        DomNode cur = parent.get(n);
        while (cur != null) {
            if ("Grid".equals(cur.containerRole)) {
                return "grid:" + safe(scopeLabel(cur));
            }
            if ("TabPage".equals(cur.containerRole) || notBlank(cur.ownerTab)) {
                return "tab:" + safe(cur.ownerTab != null ? cur.ownerTab : scopeLabel(cur));
            }
            if ("Window".equals(cur.semanticType) || "Dialog".equals(cur.semanticType)
                    || "ExtendedFrame".equals(cur.simpleClassName)) {
                return "form:" + safe(scopeLabel(cur));
            }
            cur = parent.get(cur);
        }
        // Inherit ownerTab even if no explicit TabPage container node exists.
        if (notBlank(n.ownerTab))
            return "tab:" + safe(n.ownerTab);
        return "form:root";
    }

    private static String scopeLabel(DomNode n) {
        if (notBlank(n.title))
            return n.title;
        if (notBlank(n.accessibleName))
            return n.accessibleName;
        if (notBlank(n.displayName))
            return n.displayName;
        if (notBlank(n.name))
            return n.name;
        return n.simpleClassName;
    }

    // ── Tree / map helpers ────────────────────────────────────────────────

    private static void flatten(DomNode n, List<DomNode> out) {
        if (n == null)
            return;
        out.add(n);
        for (DomNode c : n.children)
            flatten(c, out);
    }

    private static Map<DomNode, DomNode> buildParentMap(List<DomNode> windows) {
        Map<DomNode, DomNode> parent = new HashMap<>();
        for (DomNode w : windows)
            link(w, parent);
        return parent;
    }

    private static void link(DomNode n, Map<DomNode, DomNode> parent) {
        for (DomNode c : n.children) {
            parent.put(c, n);
            link(c, parent);
        }
    }

    private static boolean notBlank(String s) {
        return s != null && !s.trim().isEmpty() && !"null".equals(s);
    }

    private static String safe(String s) {
        if (s == null)
            return "";
        // Keep semanticIds stable and delimiter-safe.
        return s.replace("::", ":").replace("|", "/").trim();
    }

    /** A scope-qualified locator candidate with a frequency key. */
    private static final class Candidate {
        final String strategy;
        final String value;
        final String scope;

        Candidate(String strategy, String value, String scope) {
            this.strategy = strategy;
            this.value = value;
            this.scope = scope;
        }

        String key() {
            return scope + "" + strategy + "" + value;
        }
    }
}
