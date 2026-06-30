package com.pyebsdom.agent.runtime;

import javax.swing.SwingUtilities;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Supplier;

/**
 * Event Dispatch Thread helpers that are safe to call from a Java Attach
 * thread.
 *
 * <p>Every component read and every model-level action must run on the EDT.
 * Centralising the marshalling here removes the copy of this logic that used to
 * live inside the action layer, and gives one place to handle the
 * attach-thread quirk where {@code SwingUtilities.invokeAndWait} can fail with
 * a {@link NullPointerException} because the calling thread has no
 * {@code AppContext}. In that case the work is run directly on the current
 * thread as a last resort.
 */
public final class Edt {

    private Edt() { /* static utility */ }

    /**
     * Runs {@code work} on the EDT, blocking until it finishes. Safe to call
     * from both EDT and non-EDT threads. Exceptions thrown by {@code work} are
     * rethrown to the caller.
     */
    public static void run(Runnable work) throws Exception {
        try {
            if (SwingUtilities.isEventDispatchThread()) {
                work.run();
                return;
            }
            final CountDownLatch latch = new CountDownLatch(1);
            final AtomicReference<RuntimeException> err = new AtomicReference<>();
            SwingUtilities.invokeLater(() -> {
                try {
                    work.run();
                } catch (RuntimeException e) {
                    err.set(e);
                } finally {
                    latch.countDown();
                }
            });
            latch.await();
            if (err.get() != null) throw err.get();
        } catch (NullPointerException appContextMissing) {
            System.err.println("[ebs-dom-agent] AWT AppContext unavailable on attach thread; "
                    + "running on current thread.");
            work.run();
        }
    }

    /**
     * Computes a value on the EDT and returns it. Convenience wrapper around
     * {@link #run(Runnable)} for the common "read one thing off the EDT" case.
     */
    public static <T> T get(Supplier<T> work) throws Exception {
        final AtomicReference<T> ref = new AtomicReference<>();
        run(() -> ref.set(work.get()));
        return ref.get();
    }
}
