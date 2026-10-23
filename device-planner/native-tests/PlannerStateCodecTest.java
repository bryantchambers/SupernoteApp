package com.supernoteplanner;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.ByteArrayInputStream;
import java.util.Arrays;
import java.util.Random;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;

/** Real JCE round trips, legacy compatibility and adversarial framing, no test dependencies. */
public final class PlannerStateCodecTest {
    private interface Action { void run() throws Exception; }
    private static int checks = 0;
    private static void check(boolean value, String message) {
        checks++;
        if (!value) throw new AssertionError(message);
    }
    private static void rejects(Action action, String message) throws Exception {
        checks++;
        try { action.run(); } catch (java.security.GeneralSecurityException | java.io.IOException expected) { return; }
        throw new AssertionError(message);
    }
    private static SecretKey key() throws Exception {
        KeyGenerator generator = KeyGenerator.getInstance("AES"); generator.init(256); return generator.generateKey();
    }
    private static byte[] random(int length) {
        byte[] value = new byte[length]; new Random(length).nextBytes(value); return value;
    }
    private static void roundTrip(byte[] value, SecretKey key) throws Exception {
        byte[] encrypted = PlannerStateCodec.encrypt(value, key);
        check(PlannerStateCodec.isCurrentFormat(encrypted), "New format marker missing");
        check(Arrays.equals(value, PlannerStateCodec.decrypt(encrypted, key)), "Round trip changed state");
        DataInputStream data = new DataInputStream(new ByteArrayInputStream(encrypted));
        data.skipBytes(32); int blocks = data.readInt();
        for (int n = 0; n < blocks; n++) {
            data.skipBytes(12); int length = data.readInt();
            check(length <= PlannerStateCodec.BLOCK + 16, "A single Keystore operation exceeds the bound");
            data.skipBytes(length);
        }
        check(data.read() == -1, "Unexpected trailing data");
    }
    public static void main(String[] args) throws Exception {
        SecretKey key = key();
        for (int length : new int[]{0, 1, 16383, 16384, 16385, 175000, 300000, 1048576}) roundTrip(random(length), key);
        StringBuilder planner = new StringBuilder("{\"outbox\":[{\"id\":\"unsent-1\",\"text\":\"14:00 Arbejde @work\"}],\"pages\":[");
        for (int n = 0; n < 2000; n++) planner.append("{\"id\":\"week-").append(n).append("\",\"text\":\"Møde ÆØÅ – こんにちは\",\"regions\":[0.07,0.183,0.86,0.054]},");
        planner.append("]}");
        byte[] state = planner.toString().getBytes("UTF-8");
        check(state.length > 175000, "Fixture must exercise the observed large state");
        roundTrip(state, key);
        byte[] encoded = PlannerStateCodec.encrypt(state, key);
        check(encoded.length < state.length / 3, "Representative planner did not compress");
        Cipher legacy = Cipher.getInstance("AES/GCM/NoPadding"); legacy.init(Cipher.ENCRYPT_MODE, key);
        ByteArrayOutputStream old = new ByteArrayOutputStream(); old.write(legacy.getIV()); old.write(legacy.doFinal(state));
        byte[] legacyBytes = old.toByteArray();
        check(!PlannerStateCodec.isCurrentFormat(legacyBytes), "Legacy detected as new");
        check(Arrays.equals(state, PlannerStateCodec.decrypt(legacyBytes, key)), "Legacy state was lost");
        roundTrip(PlannerStateCodec.decrypt(legacyBytes, key), key);
        rejects(() -> PlannerStateCodec.decrypt(encoded, key()), "Wrong key was accepted");
        byte[] changedHeader = encoded.clone(); changedHeader[25] ^= 1;
        rejects(() -> PlannerStateCodec.decrypt(changedHeader, key), "Changed authenticated header was accepted");
        byte[] changedTag = encoded.clone(); changedTag[changedTag.length - 1] ^= 1;
        rejects(() -> PlannerStateCodec.decrypt(changedTag, key), "Changed tag was accepted");
        rejects(() -> PlannerStateCodec.decrypt(Arrays.copyOf(encoded, encoded.length - 1), key), "Truncation was accepted");
        rejects(() -> PlannerStateCodec.decrypt(Arrays.copyOf(encoded, encoded.length + 1), key), "Trailing bytes were accepted");
        byte[] many = PlannerStateCodec.encrypt(random(200000), key);
        // All full-size block records have the same length: IV + length + data + tag.
        int record = 12 + 4 + PlannerStateCodec.BLOCK + 16;
        byte[] swapped = many.clone();
        System.arraycopy(many, 36 + record, swapped, 36, record);
        System.arraycopy(many, 36, swapped, 36 + record, record);
        rejects(() -> PlannerStateCodec.decrypt(swapped, key), "Reordered blocks were accepted");
        byte[] another = PlannerStateCodec.encrypt(random(200000), key);
        byte[] mixed = many.clone(); System.arraycopy(another, 36, mixed, 36, record);
        rejects(() -> PlannerStateCodec.decrypt(mixed, key), "Mixed writes were accepted");
        rejects(() -> PlannerStateCodec.encrypt(new byte[PlannerStateCodec.MAX_STATE + 1], key), "Oversized state was accepted");
        System.out.println("Planner storage codec: " + checks + " checks passed; large state, legacy, tampering and bounded operations verified.");
    }
}
