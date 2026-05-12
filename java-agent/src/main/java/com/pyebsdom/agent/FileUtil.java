package com.pyebsdom.agent;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

/**
 * File I/O utilities used by the agent to write result payloads.
 *
 * <p>All methods are synchronous and block until the file is fully flushed.
 * They do not keep any resources open after returning.
 */
public final class FileUtil {

    private FileUtil() { /* static utility */ }

    /**
     * Writes {@code content} to the file at {@code path} using UTF-8 encoding,
     * creating the file and any missing parent directories as needed.
     *
     * <p>If the file already exists it is overwritten completely (truncated
     * then written), so partial reads by the caller are not possible once this
     * method returns.
     *
     * @param path    absolute or relative path for the output file
     * @param content text to write (UTF-8 encoded)
     * @throws IOException if the directory cannot be created or the file
     *                     cannot be written
     */
    public static void writeUtf8(String path, String content) throws IOException {
        if (path == null || path.trim().isEmpty()) {
            throw new IllegalArgumentException("Output file path must not be null or empty");
        }
        if (content == null) {
            content = "";
        }

        File file   = new File(path);
        File parent = file.getParentFile();

        if (parent != null && !parent.exists()) {
            if (!parent.mkdirs()) {
                // mkdirs can return false if another thread created the dir
                // concurrently; verify existence before failing.
                if (!parent.isDirectory()) {
                    throw new IOException(
                            "Could not create parent directories: " + parent.getAbsolutePath());
                }
            }
        }

        byte[] bytes = content.getBytes(StandardCharsets.UTF_8);
        try (FileOutputStream fos = new FileOutputStream(file, /* append= */ false)) {
            fos.write(bytes);
            fos.flush();
        }
    }
}
