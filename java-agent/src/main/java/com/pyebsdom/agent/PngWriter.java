package com.pyebsdom.agent;

import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.util.zip.CRC32;
import java.util.zip.DeflaterOutputStream;

final class PngWriter {

    private static final byte[] PNG_SIGNATURE = new byte[] {
            (byte) 137, 80, 78, 71, 13, 10, 26, 10
    };

    private PngWriter() {}

    static void write(BufferedImage image, File file) throws IOException {
        FileOutputStream out = new FileOutputStream(file);
        try {
            out.write(PNG_SIGNATURE);
            writeChunk(out, "IHDR", ihdr(image.getWidth(), image.getHeight()));
            writeChunk(out, "IDAT", compress(rawRgbData(image)));
            writeChunk(out, "IEND", new byte[0]);
        } finally {
            out.close();
        }
    }

    private static byte[] ihdr(int width, int height) throws IOException {
        ByteArrayOutputStream data = new ByteArrayOutputStream(13);
        writeInt(data, width);
        writeInt(data, height);
        data.write(8); // bit depth
        data.write(2); // truecolor RGB
        data.write(0); // compression
        data.write(0); // filter
        data.write(0); // interlace
        return data.toByteArray();
    }

    private static byte[] rawRgbData(BufferedImage image) throws IOException {
        int width = image.getWidth();
        int height = image.getHeight();
        int[] row = new int[width];
        ByteArrayOutputStream data = new ByteArrayOutputStream(height * (1 + width * 3));

        for (int y = 0; y < height; y++) {
            data.write(0); // filter type: none
            image.getRGB(0, y, width, 1, row, 0, width);
            for (int x = 0; x < width; x++) {
                int rgb = row[x];
                data.write((rgb >> 16) & 0xff);
                data.write((rgb >> 8) & 0xff);
                data.write(rgb & 0xff);
            }
        }

        return data.toByteArray();
    }

    private static byte[] compress(byte[] raw) throws IOException {
        ByteArrayOutputStream compressed = new ByteArrayOutputStream(raw.length / 4);
        DeflaterOutputStream deflater = new DeflaterOutputStream(compressed);
        try {
            deflater.write(raw);
        } finally {
            deflater.close();
        }
        return compressed.toByteArray();
    }

    private static void writeChunk(OutputStream out, String type, byte[] data) throws IOException {
        byte[] typeBytes = type.getBytes("ISO-8859-1");
        writeInt(out, data.length);
        out.write(typeBytes);
        out.write(data);

        CRC32 crc = new CRC32();
        crc.update(typeBytes);
        crc.update(data);
        writeInt(out, (int) crc.getValue());
    }

    private static void writeInt(OutputStream out, int value) throws IOException {
        out.write((value >>> 24) & 0xff);
        out.write((value >>> 16) & 0xff);
        out.write((value >>> 8) & 0xff);
        out.write(value & 0xff);
    }
}
