package com.pyebsdom.agent.model;

/**
 * Immutable rectangle representing a component's position and size.
 *
 * <p>Two coordinate spaces are captured:
 * <ul>
 *   <li>{@link #x}, {@link #y}, {@link #width}, {@link #height} —
 *       position relative to the component's parent container
 *       ({@link java.awt.Component#getBounds()}).</li>
 *   <li>{@link #screenX}, {@link #screenY}, {@link #screenWidth},
 *       {@link #screenHeight} — position in screen (global) coordinates
 *       ({@link java.awt.Component#getLocationOnScreen()}).
 *       May be {@code -1} if the component is not showing on screen.</li>
 * </ul>
 *
 * <p>Pure data: JSON rendering lives in {@code render.DomJson}.
 */
public final class Bounds {

    public final int x;
    public final int y;
    public final int width;
    public final int height;

    public final int screenX;
    public final int screenY;
    public final int screenWidth;
    public final int screenHeight;

    /** Creates a {@code Bounds} with both parent-relative and screen coordinates. */
    public Bounds(int x, int y, int width, int height,
                  int screenX, int screenY, int screenWidth, int screenHeight) {
        this.x            = x;
        this.y            = y;
        this.width        = width;
        this.height       = height;
        this.screenX      = screenX;
        this.screenY      = screenY;
        this.screenWidth  = screenWidth;
        this.screenHeight = screenHeight;
    }

    /**
     * Creates a {@code Bounds} with only parent-relative coordinates.
     * Screen fields are set to {@code -1}.
     */
    public Bounds(int x, int y, int width, int height) {
        this(x, y, width, height, -1, -1, width, height);
    }

    /** True when screen coordinates were captured (component was showing). */
    public boolean hasScreen() {
        return screenX >= 0;
    }

    @Override
    public String toString() {
        return "Bounds{x=" + x + ",y=" + y + ",w=" + width + ",h=" + height + "}";
    }
}
