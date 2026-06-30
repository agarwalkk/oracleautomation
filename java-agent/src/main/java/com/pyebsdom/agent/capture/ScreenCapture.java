package com.pyebsdom.agent.capture;

import com.pyebsdom.agent.runtime.AwtContext;
import com.pyebsdom.agent.runtime.Edt;

import java.awt.AWTException;
import java.awt.Component;
import java.awt.Dimension;
import java.awt.Point;
import java.awt.Rectangle;
import java.awt.Robot;
import java.awt.Toolkit;
import java.awt.image.BufferedImage;
import java.io.File;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Captures a PNG screenshot of the whole screen or one component's bounds.
 *
 * <p>This is the agent's only remaining use of {@link java.awt.Robot}, and it
 * is deliberate: {@code Robot.createScreenCapture} <b>reads pixels</b> — it does
 * not synthesise input. Screenshots are a recorder/diagnostic concern, entirely
 * separate from the non-robotic action layer, so the capability lives here in
 * its own package rather than alongside component actions.
 *
 * <p>The {@code Robot} is created and the capture taken on the EDT, because AWT
 * static initialisers may not yet be ready on a bare attach thread.
 */
public final class ScreenCapture {

    private ScreenCapture() { /* static utility */ }

    /** Outcome of a capture: the mode used and the resulting image size. */
    public static final class Result {
        public final String mode;
        public final int width;
        public final int height;

        Result(String mode, int width, int height) {
            this.mode = mode;
            this.width = width;
            this.height = height;
        }
    }

    /**
     * Captures {@code comp}'s screen bounds when it is showing, otherwise the
     * full primary screen, and writes the PNG to {@code outPath}.
     */
    public static Result capture(final Component comp, String outPath) throws Exception {
        final AtomicReference<BufferedImage> imgRef = new AtomicReference<>();
        final AtomicReference<String> modeRef = new AtomicReference<>("fullscreen");

        Edt.run(() -> {
            AwtContext.ensureAppContext();
            Robot robot;
            try {
                robot = new Robot();
            } catch (AWTException e) {
                throw new RuntimeException("Robot init failed: " + e.getMessage(), e);
            }
            if (comp != null && comp.isShowing()) {
                try {
                    Point loc = comp.getLocationOnScreen();
                    Rectangle rect = new Rectangle(loc.x, loc.y, comp.getWidth(), comp.getHeight());
                    imgRef.set(robot.createScreenCapture(clamp(rect)));
                    modeRef.set("component");
                } catch (Exception e) {
                    imgRef.set(robot.createScreenCapture(fullScreen()));
                    modeRef.set("fullscreen-fallback");
                }
            } else {
                imgRef.set(robot.createScreenCapture(fullScreen()));
            }
        });

        BufferedImage img = imgRef.get();
        File pngFile = new File(outPath);
        File parent = pngFile.getParentFile();
        if (parent != null && !parent.exists()) parent.mkdirs();
        PngWriter.write(img, pngFile);

        return new Result(modeRef.get(), img.getWidth(), img.getHeight());
    }

    private static Rectangle fullScreen() {
        Dimension d = Toolkit.getDefaultToolkit().getScreenSize();
        return new Rectangle(0, 0, d.width, d.height);
    }

    private static Rectangle clamp(Rectangle rect) {
        Dimension screen = Toolkit.getDefaultToolkit().getScreenSize();
        int x = Math.max(0, rect.x);
        int y = Math.max(0, rect.y);
        int w = Math.min(rect.width, screen.width - x);
        int h = Math.min(rect.height, screen.height - y);
        if (w <= 0 || h <= 0) return fullScreen();
        return new Rectangle(x, y, w, h);
    }
}
