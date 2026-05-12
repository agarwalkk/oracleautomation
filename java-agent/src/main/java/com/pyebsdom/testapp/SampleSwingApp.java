package com.pyebsdom.testapp;

import javax.swing.*;
import javax.swing.table.DefaultTableModel;
import java.awt.*;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;

/**
 * Minimal Swing test application for pyebsdom development.
 *
 * <p>Provides a realistic set of named, labelled components that the
 * Python agent can discover without needing a live Oracle EBS instance.
 *
 * <h2>Run</h2>
 * <pre>
 * java -cp java-agent\target\ebs-dom-agent.jar com.pyebsdom.testapp.SampleSwingApp
 * </pre>
 *
 * <h2>Inspect in a second terminal</h2>
 * <pre>
 * pyebsdom quick
 * pyebsdom scan --contains SampleSwingApp --out sample-tree.json --layout sample-layout.txt
 * </pre>
 */
public class SampleSwingApp {

    // ── Named component constants (match test assertions) ─────────────────
    private static final String CUSTOMER_NAME_FIELD  = "CUSTOMER_NAME";
    private static final String ORDER_NUMBER_FIELD   = "ORDER_NUMBER";
    private static final String FIND_BUTTON_NAME     = "FIND_BUTTON";
    private static final String SAVE_BUTTON_NAME     = "SAVE_BUTTON";
    private static final String ORDER_LINES_TABLE    = "ORDER_LINES_TABLE";
    private static final String STATUS_LABEL_NAME    = "STATUS_LABEL";

    // ── Table columns ─────────────────────────────────────────────────────
    private static final String[] TABLE_COLUMNS =
            { "Line", "Item", "Description", "Quantity" };

    // ── Seed data ─────────────────────────────────────────────────────────
    private static final Object[][] SEED_ROWS = {
        { 1, "AS54888", "Standard Desktop Computer",  1 },
        { 2, "MB13929", "Mechanical Keyboard",         2 },
        { 3, "MON27QHD", "27\" QHD Monitor",           1 },
        { 4, "MOU-ERGO", "Ergonomic Mouse",            1 },
    };

    // ── State ─────────────────────────────────────────────────────────────
    private JTextField customerNameField;
    private JTextField orderNumberField;
    private JButton    findButton;
    private JButton    saveButton;
    private JTable     orderLinesTable;
    private JLabel     statusLabel;

    // ── Build GUI ─────────────────────────────────────────────────────────

    private JFrame buildFrame() {
        JFrame frame = new JFrame("PyEbsDom Sample App");
        frame.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);
        frame.setSize(780, 520);
        frame.setMinimumSize(new Dimension(600, 400));
        frame.setLocationRelativeTo(null);

        // ── Header panel (Customer / Order Number / buttons) ──────────────
        JPanel header = new JPanel(new GridBagLayout());
        header.setBorder(BorderFactory.createTitledBorder("Order Header"));
        GridBagConstraints gbc = new GridBagConstraints();
        gbc.insets = new Insets(4, 8, 4, 8);
        gbc.fill = GridBagConstraints.HORIZONTAL;

        // Customer
        gbc.gridx = 0; gbc.gridy = 0; gbc.weightx = 0;
        header.add(new JLabel("Customer:"), gbc);
        customerNameField = new JTextField(20);
        customerNameField.setName(CUSTOMER_NAME_FIELD);
        customerNameField.setToolTipText("Enter customer name or account number");
        gbc.gridx = 1; gbc.gridy = 0; gbc.weightx = 1.0;
        header.add(customerNameField, gbc);

        // Order Number
        gbc.gridx = 0; gbc.gridy = 1; gbc.weightx = 0;
        header.add(new JLabel("Order Number:"), gbc);
        orderNumberField = new JTextField(20);
        orderNumberField.setName(ORDER_NUMBER_FIELD);
        orderNumberField.setToolTipText("Enter sales order number");
        gbc.gridx = 1; gbc.gridy = 1; gbc.weightx = 1.0;
        header.add(orderNumberField, gbc);

        // Buttons
        JPanel buttonRow = new JPanel(new FlowLayout(FlowLayout.LEFT, 8, 0));
        findButton = new JButton("Find");
        findButton.setName(FIND_BUTTON_NAME);
        findButton.setToolTipText("Search for matching orders");
        saveButton = new JButton("Save");
        saveButton.setName(SAVE_BUTTON_NAME);
        saveButton.setToolTipText("Save the current order");
        buttonRow.add(findButton);
        buttonRow.add(saveButton);

        gbc.gridx = 0; gbc.gridy = 2; gbc.gridwidth = 2; gbc.weightx = 1.0;
        header.add(buttonRow, gbc);

        // ── Order Lines table ─────────────────────────────────────────────
        DefaultTableModel tableModel = new DefaultTableModel(SEED_ROWS, TABLE_COLUMNS) {
            @Override
            public boolean isCellEditable(int row, int column) {
                // Allow editing Quantity column only
                return column == 3;
            }
            @Override
            public Class<?> getColumnClass(int column) {
                return column == 3 ? Integer.class : Object.class;
            }
        };
        orderLinesTable = new JTable(tableModel);
        orderLinesTable.setName(ORDER_LINES_TABLE);
        orderLinesTable.setToolTipText("Sales order lines");
        orderLinesTable.setRowHeight(22);
        orderLinesTable.getTableHeader().setReorderingAllowed(false);
        // Column widths
        orderLinesTable.getColumnModel().getColumn(0).setPreferredWidth(45);
        orderLinesTable.getColumnModel().getColumn(1).setPreferredWidth(90);
        orderLinesTable.getColumnModel().getColumn(2).setPreferredWidth(340);
        orderLinesTable.getColumnModel().getColumn(3).setPreferredWidth(75);

        JPanel linesPanel = new JPanel(new BorderLayout());
        linesPanel.setBorder(BorderFactory.createTitledBorder("Order Lines"));
        linesPanel.add(new JScrollPane(orderLinesTable), BorderLayout.CENTER);

        // ── Status bar ────────────────────────────────────────────────────
        statusLabel = new JLabel("Ready.");
        statusLabel.setName(STATUS_LABEL_NAME);
        statusLabel.setBorder(BorderFactory.createCompoundBorder(
            BorderFactory.createMatteBorder(1, 0, 0, 0, Color.LIGHT_GRAY),
            BorderFactory.createEmptyBorder(3, 6, 3, 6)
        ));

        // ── Wire actions ──────────────────────────────────────────────────
        findButton.addActionListener(new ActionListener() {
            @Override
            public void actionPerformed(ActionEvent e) {
                String customer = customerNameField.getText().trim();
                String order    = orderNumberField.getText().trim();
                if (customer.isEmpty() && order.isEmpty()) {
                    setStatus("Enter a Customer or Order Number before searching.");
                } else {
                    setStatus("Found order for: "
                            + (customer.isEmpty() ? order : customer));
                }
            }
        });

        saveButton.addActionListener(new ActionListener() {
            @Override
            public void actionPerformed(ActionEvent e) {
                setStatus("Order saved at " + new java.util.Date());
            }
        });

        // ── Assemble ──────────────────────────────────────────────────────
        frame.setLayout(new BorderLayout(0, 4));
        frame.add(header,     BorderLayout.NORTH);
        frame.add(linesPanel, BorderLayout.CENTER);
        frame.add(statusLabel, BorderLayout.SOUTH);

        return frame;
    }

    private void setStatus(String message) {
        statusLabel.setText(message);
    }

    // ── Entry point ───────────────────────────────────────────────────────

    public static void main(String[] args) {
        // Use the system look-and-feel so it resembles a real EBS environment
        try {
            UIManager.setLookAndFeel(UIManager.getSystemLookAndFeelClassName());
        } catch (Exception ignored) { /* fall through to default L&F */ }

        SwingUtilities.invokeLater(new Runnable() {
            @Override
            public void run() {
                SampleSwingApp app = new SampleSwingApp();
                JFrame frame = app.buildFrame();
                frame.setVisible(true);
                System.out.println("[SampleSwingApp] running — PID not directly available.");
                System.out.println("[SampleSwingApp] find PID with:  pyebsdom processes");
                System.out.println("[SampleSwingApp] then scan with: pyebsdom quick");
            }
        });
        // Keep the main thread alive so the process does not exit while
        // the window is open (the Swing EDT keeps the JVM alive anyway,
        // but this makes the intent explicit).
    }
}
