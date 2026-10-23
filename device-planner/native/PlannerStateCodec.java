package com.supernoteplanner;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.zip.GZIPInputStream;
import java.util.zip.GZIPOutputStream;
import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Versioned, compressed, authenticated blocks. Uses AndroidKeyStore keys on-device. */
public final class PlannerStateCodec {
    static final byte[] MAGIC = new byte[]{'S','N','P','E','N','C','0','2'};
    static final int BLOCK = 16 * 1024;
    static final int MAX_STATE = 8 * 1024 * 1024;
    private static final int MAX_COMPRESSED = MAX_STATE + 65536;

    private PlannerStateCodec() {}

    public static boolean isCurrentFormat(byte[] bytes) {
        return bytes.length >= MAGIC.length && Arrays.equals(Arrays.copyOf(bytes, MAGIC.length), MAGIC);
    }

    private static byte[] aad(byte[] header, int index, int length) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        DataOutputStream data = new DataOutputStream(out);
        data.write(header); data.writeInt(index); data.writeInt(length);
        return out.toByteArray();
    }

    public static byte[] encrypt(byte[] plaintext, SecretKey key) throws GeneralSecurityException, IOException {
        if (plaintext.length > MAX_STATE) throw new IOException("Planner state exceeds the safe storage limit");
        ByteArrayOutputStream zipped = new ByteArrayOutputStream();
        try (GZIPOutputStream gzip = new GZIPOutputStream(zipped)) { gzip.write(plaintext); }
        byte[] compressed = zipped.toByteArray();
        if (compressed.length > MAX_COMPRESSED) throw new IOException("Compressed planner state is too large");
        int count = (compressed.length + BLOCK - 1) / BLOCK;
        ByteArrayOutputStream headerOut = new ByteArrayOutputStream();
        DataOutputStream headerData = new DataOutputStream(headerOut);
        byte[] writeId = new byte[16]; new SecureRandom().nextBytes(writeId);
        headerData.write(MAGIC); headerData.write(writeId);
        headerData.writeInt(plaintext.length); headerData.writeInt(compressed.length); headerData.writeInt(count);
        byte[] header = headerOut.toByteArray();
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        DataOutputStream data = new DataOutputStream(out); data.write(header);
        for (int index = 0; index < count; index++) {
            int offset = index * BLOCK, length = Math.min(BLOCK, compressed.length - offset);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key); // Keystore supplies a fresh nonce for every block.
            cipher.updateAAD(aad(header, index, length));
            byte[] encrypted = cipher.doFinal(compressed, offset, length);
            byte[] iv = cipher.getIV();
            if (iv.length != 12 || encrypted.length != length + 16) throw new IOException("Unsupported planner cipher framing");
            data.write(iv); data.writeInt(encrypted.length); data.write(encrypted);
        }
        return out.toByteArray();
    }

    public static byte[] decrypt(byte[] bytes, SecretKey key) throws GeneralSecurityException, IOException {
        if (bytes.length > MAX_COMPRESSED + 65536) throw new IOException("Encrypted planner state is too large");
        if (!isCurrentFormat(bytes)) {
            // Legacy IV+ciphertext remains readable. Never delete or reinterpret it.
            if (bytes.length < 28) throw new IOException("Corrupt legacy planner state; keep storage intact");
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, Arrays.copyOf(bytes, 12)));
            // Avoid large provider update calls when opening old files too.
            // No plaintext leaves this method unless the final tag verifies.
            ByteArrayOutputStream legacy = new ByteArrayOutputStream();
            int offset = 12;
            while (bytes.length - offset > 16) {
                int length = Math.min(BLOCK, bytes.length - offset - 16);
                byte[] part = cipher.update(bytes, offset, length);
                if (part != null) legacy.write(part);
                offset += length;
            }
            legacy.write(cipher.doFinal(bytes, offset, bytes.length - offset));
            return legacy.toByteArray();
        }
        DataInputStream data = new DataInputStream(new ByteArrayInputStream(bytes));
        byte[] header = new byte[36]; data.readFully(header);
        DataInputStream fields = new DataInputStream(new ByteArrayInputStream(header));
        fields.skipBytes(24);
        int plaintextLength = fields.readInt(), compressedLength = fields.readInt(), count = fields.readInt();
        if (plaintextLength < 0 || plaintextLength > MAX_STATE || compressedLength < 1 || compressedLength > MAX_COMPRESSED ||
                count != (compressedLength + BLOCK - 1) / BLOCK) throw new IOException("Invalid planner block header; keep storage intact");
        ByteArrayOutputStream zipped = new ByteArrayOutputStream();
        for (int index = 0; index < count; index++) {
            int expected = Math.min(BLOCK, compressedLength - index * BLOCK);
            byte[] iv = new byte[12]; data.readFully(iv);
            int length = data.readInt();
            if (length != expected + 16) throw new IOException("Invalid planner block length; keep storage intact");
            byte[] encrypted = new byte[length]; data.readFully(encrypted);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, iv));
            cipher.updateAAD(aad(header, index, expected));
            zipped.write(cipher.doFinal(encrypted));
        }
        if (data.read() != -1 || zipped.size() != compressedLength) throw new IOException("Incomplete planner state; keep storage intact");
        ByteArrayOutputStream plaintext = new ByteArrayOutputStream();
        try (GZIPInputStream gzip = new GZIPInputStream(new ByteArrayInputStream(zipped.toByteArray()))) {
            byte[] buffer = new byte[8192]; int length;
            while ((length = gzip.read(buffer)) != -1) {
                if (plaintext.size() + length > plaintextLength) throw new IOException("Invalid planner expansion length");
                plaintext.write(buffer, 0, length);
            }
        }
        if (plaintext.size() != plaintextLength) throw new IOException("Incomplete planner plaintext; keep storage intact");
        return plaintext.toByteArray();
    }
}
