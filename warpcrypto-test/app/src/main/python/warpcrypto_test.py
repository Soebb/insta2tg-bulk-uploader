import os
import platform
import sys
import warpcrypto


def run_tests():
    lines = [
        "WarpCrypto Android test",
        "-----------------------",
        f"Python: {sys.version.split()[0]}",
        f"Platform: {platform.machine()}",
        f"Module: {warpcrypto.__file__}",
        "",
    ]

    data = bytes((i * 37 + 11) % 256 for i in range(4096))
    key = bytes(range(32))
    iv = bytes(range(32, 64))

    encrypted = warpcrypto.ige256_encrypt(data, key, iv)
    decrypted = warpcrypto.ige256_decrypt(encrypted, key, iv)

    assert len(encrypted) == len(data), "IGE output length mismatch"
    assert decrypted == data, "IGE round-trip mismatch"
    lines.append("PASS: AES-256-IGE encrypt/decrypt")

    auth_key = bytes((i * 13 + 7) % 256 for i in range(256))
    msg_key = os.urandom(16)
    aes_key, aes_iv = warpcrypto.kdf(auth_key, msg_key, True)
    assert len(aes_key) == 32, "KDF AES key must be 32 bytes"
    assert len(aes_iv) == 32, "KDF AES IV must be 32 bytes"
    lines.append("PASS: MTProto 2.0 KDF")

    lines.extend([
        "",
        "ALL TESTS PASSED",
        "The Android native extension loaded and executed successfully.",
    ])
    return "\n".join(lines)
