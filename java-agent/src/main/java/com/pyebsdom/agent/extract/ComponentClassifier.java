package com.pyebsdom.agent.extract;

import javax.accessibility.Accessible;
import javax.accessibility.AccessibleContext;
import javax.accessibility.AccessibleRole;
import javax.swing.*;
import javax.swing.text.JTextComponent;
import java.awt.*;

/**
 * Classifies an AWT/Swing component into a high-level semantic type string.
 *
 * <p>Classification order (first match wins):
 * <ol>
 *   <li>Exact Swing/AWT {@code instanceof} checks (most reliable).</li>
 *   <li>Fully-qualified class name substring checks (Oracle Forms /
 *       Oracle EWT class names that don't inherit from standard Swing).</li>
 *   <li>Accessible role (fallback when class hierarchy is opaque).</li>
 *   <li>{@code "Unknown"} if nothing matched.</li>
 * </ol>
 *
 * <p>The returned strings match the vocabulary defined in the Phase 4
 * requirements: Window, Dialog, Form, Canvas, Panel, Region, Field,
 * TextArea, Button, Checkbox, RadioButton, List, LOV, ComboBox, Menu,
 * MenuItem, Toolbar, Tab, Grid, Table, Tree, Label, MessageBar,
 * StatusBar, Unknown.
 */
public final class ComponentClassifier {

    private ComponentClassifier() {}

    /**
     * Returns the semantic type string for {@code component}.
     *
     * @param component any AWT component; must not be {@code null}
     * @return a non-null, non-empty semantic type string
     */
    public static String classify(Component component) {
        // ── 1. Exact AWT/Swing instanceof checks ──────────────────────────
        if (component instanceof JFrame)         return "Window";
        if (component instanceof Frame)          return "Window";
        if (component instanceof JDialog)        return "Dialog";
        if (component instanceof Dialog)         return "Dialog";
        if (component instanceof JPopupMenu)     return "Menu";
        if (component instanceof JMenuBar)       return "Toolbar";
        if (component instanceof JMenu)          return "Menu";
        if (component instanceof JMenuItem)      return "MenuItem";
        if (component instanceof JRadioButton)   return "RadioButton";
        if (component instanceof JCheckBox)      return "Checkbox";
        if (component instanceof JToggleButton)  return "Checkbox";
        if (component instanceof JButton)        return "Button";
        if (component instanceof AbstractButton) return "Button";
        if (component instanceof JComboBox)      return "ComboBox";
        if (component instanceof JList)          return "List";
        if (component instanceof JTree)          return "Tree";
        if (component instanceof JTable)         return "Table";
        if (component instanceof JTextArea)      return "TextArea";
        if (component instanceof JTextComponent) return "Field";
        if (component instanceof JTabbedPane)    return "Tab";
        if (component instanceof JToolBar)       return "Toolbar";
        if (component instanceof JLabel)         return "Label";

        // ── 2. Exact fully-qualified class name matches (Oracle live classes) ─
        // Must run BEFORE the generic Container/Window catch-alls so that
        // Oracle EWT/Forms classes (which all extend java.awt.Container) are
        // given precise types rather than falling through to "Panel".
        String fqn = component.getClass().getName();
        String fqnResult = classifyByExactFqn(fqn);
        if (fqnResult != null) return fqnResult;

        // ── 3. Class name substring checks ────────────────────────────────
        String cn = fqn.toLowerCase();
        if (cn.startsWith("oracle.forms") || cn.startsWith("oracle.ewt")
                || cn.startsWith("oracle.apps")) {
            return classifyOracleByName(cn);
        }

        // ── 4. Standard AWT/Swing container catch-alls ────────────────────
        if (component instanceof JScrollPane)    return "Panel";
        if (component instanceof JSplitPane)     return "Panel";
        if (component instanceof JLayeredPane)   return "Panel";
        if (component instanceof JRootPane)      return "Panel";
        if (component instanceof JPanel)         return "Panel";
        if (component instanceof Canvas)         return "Canvas";
        if (component instanceof Window)         return "Window";
        if (component instanceof Container)      return "Panel";

        // ── 5. Generic class-name heuristics (non-Oracle) ─────────────────
        if (cn.contains("grid"))       return "Grid";
        if (cn.contains("table"))      return "Table";
        if (cn.contains("textfield") || cn.contains("textbox") || cn.contains("inputfield"))
                                        return "Field";
        if (cn.contains("textarea"))   return "TextArea";
        if (cn.contains("button"))     return "Button";
        if (cn.contains("checkbox"))   return "Checkbox";
        if (cn.contains("combobox") || cn.contains("dropdown"))
                                        return "ComboBox";
        if (cn.contains("list"))       return "List";
        if (cn.contains("tree"))       return "Tree";
        if (cn.contains("tab"))        return "Tab";
        if (cn.contains("toolbar"))    return "Toolbar";
        if (cn.contains("menu"))       return "Menu";
        if (cn.contains("label"))      return "Label";
        if (cn.contains("statusbar") || cn.contains("status"))
                                        return "StatusBar";
        if (cn.contains("messagebar") || cn.contains("message"))
                                        return "MessageBar";
        if (cn.contains("canvas"))     return "Canvas";
        if (cn.contains("form"))       return "Form";
        if (cn.contains("panel") || cn.contains("region") || cn.contains("pane"))
                                        return "Panel";

        // ── 6. Accessible role fallback ────────────────────────────────────
        if (component instanceof Accessible) {
            try {
                AccessibleContext ac = ((Accessible) component).getAccessibleContext();
                if (ac != null) {
                    AccessibleRole role = ac.getAccessibleRole();
                    if (role != null) {
                        String roleName = role.toDisplayString();
                        if (roleName != null) {
                            return classifyAccessibleRole(roleName.toLowerCase());
                        }
                    }
                }
            } catch (Throwable ignored) {}
        }

        return "Unknown";
    }

    // ── Exact FQN table (from live EBS class inventory) ───────────────────

    /**
     * Matches fully-qualified class names observed in real Oracle EBS R12
     * Java Web Start sessions.  Returns {@code null} when the class is not
     * in the table so the caller can fall through to substring matching.
     */
    private static String classifyByExactFqn(String fqn) {
        switch (fqn) {
            // ── oracle.forms.ui ──────────────────────────────────────────
            case "oracle.forms.ui.VTextField":          return "Field";
            case "oracle.forms.ui.VTextArea":           return "TextArea";
            case "oracle.forms.ui.FLWTextArea":         return "TextArea";
            case "oracle.forms.ui.VButton":             return "Button";
            case "oracle.forms.ui.VPopList":            return "ComboBox";
            case "oracle.forms.ui.VTList":              return "List";
            case "oracle.forms.ui.ExtendedCheckbox":    return "Checkbox";
            case "oracle.forms.ui.DrawnPanel":          return "Canvas";
            case "oracle.forms.ui.FScrollBox":          return "Panel";
            case "oracle.forms.ui.ExtendedFrame":       return "Window";
            case "oracle.forms.ui.FormDesktopContainer": return "Panel";
            case "oracle.forms.ui.FormsTabPanel":       return "Tab";
            case "oracle.forms.ui.FormStatusArea":      return "StatusBar";
            case "oracle.forms.ui.StatsIndicator":      return "StatusBar";
            case "oracle.forms.ui.mdi.MDIContainer":    return "Panel";
            // ── oracle.ewt ───────────────────────────────────────────────
            case "oracle.ewt.lwAWT.LWCheckbox":         return "Checkbox";
            case "oracle.ewt.lwAWT.LWComponent":        return "Panel";
            case "oracle.ewt.lwAWT.LWContainer":        return "Panel";
            case "oracle.ewt.lwAWT.LWLabel":            return "Label";
            case "oracle.ewt.lwAWT.LWScrollbar":        return "Panel";
            case "oracle.ewt.lwAWT.LWDataSourceChoice$ChoiceButton": return "ComboBox";
            case "oracle.ewt.lwAWT.LWDataSourceList$Content":        return "List";
            case "oracle.ewt.lwAWT.lwText.LWTextField": return "Field";
            case "oracle.ewt.lwAWT.lwWindow.LWWindow$FocusTransferComp": return "Panel";
            case "oracle.ewt.lwAWT.lwWindow.laf.TitleBar":             return "Panel";
            case "oracle.ewt.lwAWT.lwWindow.laf.TitleBar$SystemMenuBar": return "Toolbar";
            case "oracle.ewt.lwAWT.lwWindow.laf.TitleBar$SystemMenu":   return "Menu";
            case "oracle.ewt.lwAWT.lwWindow.laf.TitleBar$CaptionComp":  return "Label";
            case "oracle.ewt.lwAWT.lwMenu.LWMenu":      return "Menu";
            case "oracle.ewt.lwAWT.lwMenu.LWMenuBar":   return "Toolbar";
            case "oracle.ewt.button.ContinuousButton":  return "Button";
            case "oracle.ewt.button.PushButton":        return "Button";
            case "oracle.ewt.toolBar.ToolBar":          return "Toolbar";
            case "oracle.ewt.tabBar.TabBar":            return "Tab";
            case "oracle.ewt.tabPanel.TabPanelSheet":   return "Tab";
            case "oracle.ewt.laf.basic.BasicTabBarUI$Scroller": return "Panel";
            case "oracle.ewt.statusBar.StatusBar":      return "StatusBar";
            case "oracle.ewt.imageCanvas.ImageCanvas":  return "Canvas";
            case "oracle.ewt.EwtComponent":             return "Panel";
            case "oracle.ewt.scrolling.scrollBox.ScrollBox":   return "Panel";
            case "oracle.ewt.scrolling.scrollBox.ScrollBox$1": return "Panel";
            case "oracle.ewt.scrolling.scrollBox.EwtLWScrollbar": return "Panel";
            case "oracle.ewt.event.tracking.GlassMouseGrabProvider$Proxy": return "Panel";
            // ── oracle.apps.fnd ──────────────────────────────────────────
            case "oracle.apps.fnd.ui.CheckBox":         return "Checkbox";
            case "oracle.apps.fnd.ui.Button":           return "Button";
            case "oracle.apps.fnd.ui.DynamicPrompt":    return "Label";
            case "oracle.apps.fnd.ui.RadioGroup":       return "RadioButton";
            case "oracle.apps.fnd.formsClient.AppletAdapter":            return "Panel";
            case "oracle.apps.fnd.formsClient.jnlp.FndFormsEngine":     return "Panel";
            case "oracle.apps.fnd.soa.forms.services.rt.CommBean":       return "Panel";
            // ── oracle.forms.engine ──────────────────────────────────────
            case "oracle.forms.engine.Splashscreen":    return "Panel";
            default: return null;
        }
    }

    // ── Oracle-specific name classification (substring fallback) ──────────

    private static String classifyOracleByName(String cn) {
        // Fields
        if (cn.contains("vtextfield") || cn.contains("lwtextfield")
                || cn.contains("textfield") || cn.contains("inputfield")
                || cn.contains("datefield") || cn.contains("numberfield")
                || cn.contains("charfield"))            return "Field";
        if (cn.contains("vtextarea") || cn.contains("textarea"))
                                                        return "TextArea";
        if (cn.contains("lov"))                         return "LOV";
        // Buttons
        if (cn.contains("vbutton") || cn.contains("pushbutton")
                || cn.contains("continuousbutton") || cn.contains("button"))
                                                        return "Button";
        // Selection controls
        if (cn.contains("lwcheckbox") || cn.contains("extendedcheckbox")
                || cn.contains("checkbox"))             return "Checkbox";
        if (cn.contains("radiogroup") || cn.contains("radiobutton")
                || cn.contains("radio"))                return "RadioButton";
        // Dropdowns / lists
        if (cn.contains("vpoplist") || cn.contains("datasourcechoice")
                || cn.contains("combobox") || cn.contains("dropdown"))
                                                        return "ComboBox";
        if (cn.contains("vtlist") || cn.contains("datasourcelist")
                || cn.contains("list"))                 return "List";
        // Grid / table
        if (cn.contains("grid") || cn.contains("multirecord"))
                                                        return "Grid";
        if (cn.contains("table"))                       return "Table";
        // Tree
        if (cn.contains("tree"))                       return "Tree";
        // Tab
        if (cn.contains("formstabpanel") || cn.contains("tabbar")
                || cn.contains("tabpanel"))             return "Tab";
        // Toolbar / menu
        if (cn.contains("toolbar"))                    return "Toolbar";
        if (cn.contains("menuitem"))                   return "MenuItem";
        if (cn.contains("menubar"))                    return "Toolbar";
        if (cn.contains("lwmenu") || cn.contains("menu"))
                                                       return "Menu";
        // Status / message bars
        if (cn.contains("statusbar") || cn.contains("statusarea")
                || cn.contains("statsindicator"))      return "StatusBar";
        if (cn.contains("messageline") || cn.contains("messagebar"))
                                                       return "MessageBar";
        // Labels / prompts
        if (cn.contains("dynamicprompt") || cn.contains("lwlabel")
                || cn.contains("label"))               return "Label";
        // Canvas / image
        if (cn.contains("drawnpanel") || cn.contains("imagecanvas")
                || cn.contains("canvas"))              return "Canvas";
        // Windows / dialogs
        if (cn.contains("extendedframe") || cn.contains("window"))
                                                       return "Window";
        if (cn.contains("dialog"))                     return "Dialog";
        // Tab panel sheet
        if (cn.contains("tabpanelsheet"))              return "Tab";
        // Scrollbox / scrollbar (structural, not meaningful alone)
        if (cn.contains("scrollbox") || cn.contains("scrollbar"))
                                                       return "Panel";
        // Forms engine containers
        if (cn.contains("mdicontainer") || cn.contains("desktopcontainer"))
                                                       return "Panel";
        if (cn.contains("form"))                       return "Form";
        if (cn.contains("panel") || cn.contains("region") || cn.contains("block"))
                                                       return "Region";
        return "Panel";
    }

    // ── Accessible role mapping ────────────────────────────────────────────

    /**
     * Maps an accessible-role display string (lowercase) to a semantic type.
     *
     * <p>Made public so {@link DomScanner} can classify menu/toolbar items
     * discovered via the Accessibility API that are not real AWT Components.
     */
    public static String classifyAccessibleRole(String role) {
        if (role.contains("frame"))       return "Window";
        if (role.contains("dialog"))      return "Dialog";
        if (role.contains("push button") || role.contains("pushbutton"))
                                           return "Button";
        if (role.contains("check box") || role.contains("checkbox"))
                                           return "Checkbox";
        if (role.contains("radio button") || role.contains("radiobutton"))
                                           return "RadioButton";
        if (role.contains("text"))         return "Field";
        if (role.contains("combo box") || role.contains("combobox"))
                                           return "ComboBox";
        if (role.contains("list"))         return "List";
        if (role.contains("tree"))         return "Tree";
        if (role.contains("table"))        return "Table";
        if (role.contains("menu bar"))     return "Toolbar";
        if (role.contains("menu item"))    return "MenuItem";
        if (role.contains("menu"))         return "Menu";
        if (role.contains("label"))        return "Label";
        if (role.contains("page tab"))     return "Tab";
        if (role.contains("tool bar"))     return "Toolbar";
        if (role.contains("panel"))        return "Panel";
        if (role.contains("canvas"))       return "Canvas";
        return "Unknown";
    }
}
