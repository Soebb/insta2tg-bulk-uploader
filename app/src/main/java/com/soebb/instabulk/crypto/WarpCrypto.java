package com.soebb.instabulk.crypto;

/**
 * JNI facade for the Rust WarpCrypto crate.
 *
 * Python code calls this class through Chaquopy. The native methods mirror the
 * public WarpCrypto Python API while keeping the cryptographic implementation
 * in the upstream Rust crate.
 */
public final class WarpCrypto {
    static {
        System.loadLibrary("warpcrypto");
    }

    private WarpCrypto() {}

    public static native byte[] nativeIgeEncrypt(byte[] data, byte[] key, byte[] iv);
    public static native byte[] nativeIgeDecrypt(byte[] data, byte[] key, byte[] iv);
    public static native byte[][] nativeCtr(byte[] data, byte[] key, byte[] iv, byte[] state);
    public static native byte[][] nativeKdf(byte[] authKey, byte[] msgKey, boolean outgoing);
    public static native byte[] nativePackMessage(long msgId, int seqNo, byte[] body, long salt,
                                                   byte[] sessionId, byte[] authKey, byte[] authKeyId, boolean incoming);
    public static native byte[] nativeUnpackMessage(byte[] packed, byte[] sessionId,
                                                     byte[] authKey, byte[] authKeyId);
}
