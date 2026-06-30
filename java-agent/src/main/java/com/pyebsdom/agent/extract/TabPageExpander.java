package com.pyebsdom.agent.extract;

import com.pyebsdom.agent.model.DomNode;
import com.pyebsdom.agent.runtime.Reflect;

import java.awt.Component;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

/**
 * Materialises a Forms tab panel's <b>inactive</b> pages from the model, so a
 * single scan can capture every tab's fields without navigating/clicking tabs.
 *
 * <h3>Why</h3>
 * Oracle EWT swaps tab pages: only the selected page's components are reachable
 * through {@code Container.getComponents()}; the other pages live only inside
 * their {@code TabPanelPage} objects, reachable via
 * {@code FormsTabPanel.getPage(int)}. The studio used to navigate (click) every
 * tab and take a full DOM dump per tab to work around this. This expander reads
 * the pages directly — the same approach {@code TreeItemExpander} uses for tree
 * rows — so the per-tab DOM dumps can be replaced by one dump (screenshots can
 * still be taken per tab, which is cheap).
 *
 * <h3>Feasibility marker</h3>
 * Whether an <em>inactive</em> page's {@code getComponent()} returns a
 * populated
 * subtree (eager) or an empty one (lazy, instantiated only on first activation)
 * is form-dependent. {@code DomScanner} records {@code _tabPagesExpanded} and
 * {@code _tabPagesWithContent} on the panel node so a single test scan answers
 * it: if pages come back with content, the per-tab navigation can be dropped.
 */
public final class TabPageExpander {

    private TabPageExpander() {
    }

    /** One tab page: its index, title, root component, and whether it is active. */
    public static final class Page {
        public final int index;
        public final String title;
        public final Component component;
        public final boolean selected;

        Page(int index, String title, Component component, boolean selected) {
            this.index = index;
            this.title = title;
            this.component = component;
            this.selected = selected;
        }
    }

    /** True for a Forms/EWT tab panel we know how to page through. */
    public static boolean isTabPanel(DomNode node) {
        return node != null && node.simpleClassName != null
                && (node.simpleClassName.equals("FormsTabPanel")
                        || node.simpleClassName.endsWith("TabPanel"));
    }

    /**
     * Enumerate the panel's pages via {@code getPageCount()} / {@code getPage(i)}.
     * Returns an empty list when the component is not a recognisable tab panel.
     */
    public static List<Page> enumerate(Component panel) {
        List<Page> out = new ArrayList<>();
        Method getCount = Reflect.method(panel.getClass(), "getPageCount");
        Method getPage = Reflect.method(panel.getClass(), "getPage", int.class);
        if (getCount == null || getPage == null)
            return out;

        int count = Reflect.asInt(Reflect.invoke(panel, getCount), 0);
        if (count <= 0)
            return out;
        Object selected = Reflect.call(panel, "getSelectedPage");

        for (int i = 0; i < count && i < 64; i++) {
            Object page = Reflect.invoke(panel, getPage, i);
            if (page == null)
                continue;
            out.add(new Page(i, pageTitle(page, i), pageComponent(page), isSelected(page, page == selected)));
        }
        return out;
    }

    /**
     * The page's root component. Confirmed by reflection probe of EWT
     * {@code TabPanelPage}: {@code getContent():Component}. Other names are kept
     * as defensive fallbacks for non-EWT tab impls.
     */
    private static Component pageComponent(Object page) {
        if (page instanceof Component)
            return (Component) page;
        for (String m : new String[] { "getContent", "getComponent", "getControl",
                "getCanvas", "getView", "getClientComponent" }) {
            Object v = Reflect.call(page, m);
            if (v instanceof Component)
                return (Component) v;
        }
        return null;
    }

    /** The page's tab title — EWT {@code TabPanelPage.getLabel():String}. */
    private static String pageTitle(Object page, int i) {
        for (String m : new String[] { "getLabel", "getTitle", "getText", "getName" }) {
            Object v = Reflect.call(page, m);
            if (v != null) {
                String s = String.valueOf(v).trim();
                if (!s.isEmpty())
                    return s;
            }
        }
        return "Page " + i;
    }

    /** Active-page check via {@code TabPanelPage.isSelected()}, else identity. */
    private static boolean isSelected(Object page, boolean identityMatch) {
        Object v = Reflect.call(page, "isSelected");
        if (v instanceof Boolean)
            return (Boolean) v;
        return identityMatch;
    }
}
