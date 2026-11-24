"""Python API-compatible adapter backed by the Android Rust JNI library.

The module deliberately mirrors the subset of WarpCrypto used by Wzgram.
"""
import struct

from com.soebb.instabulk.crypto import WarpCrypto as _Native


def ige256_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    if len(key) != 32 or len(iv) != 32:
        raise ValueError("Key and IV must be 32 bytes")
    if len(data) % 16:
        raise ValueError("data length must be a multiple of 16 bytes")
    return bytes(_Native.nativeIgeEncrypt(data, key, iv))


def ige256_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    if len(key) != 32 or len(iv) != 32:
        raise ValueError("Key and IV must be 32 bytes")
    if len(data) % 16:
        raise ValueError("data length must be a multiple of 16 bytes")
    return bytes(_Native.nativeIgeDecrypt(data, key, iv))


def _ctr(data, key, iv, state):
    if len(key) != 32 or len(iv) != 16 or len(state) != 1:
        raise ValueError("Invalid key, IV, or state length")
    result = _Native.nativeCtr(data, key, iv, state)
    return bytes(result)


def ctr256_encrypt(data: bytes, key: bytes, iv: bytearray, state: bytearray) -> bytes:
    result = _ctr(data, key, iv, state)
    return result


def ctr256_decrypt(data: bytes, key: bytes, iv: bytearray, state: bytearray) -> bytes:
    return _ctr(data, key, iv, state)


def ctr256_encrypt_inplace(data: bytearray, key: bytes, iv: bytearray, state: bytearray) -> None:
    data[:] = _ctr(bytes(data), key, iv, state)


def ctr256_decrypt_inplace(data: bytearray, key: bytes, iv: bytearray, state: bytearray) -> None:
    ctr256_encrypt_inplace(data, key, iv, state)


def ctr256_encrypt_batch(data_flat: bytes, sizes: bytearray, key: bytes,
                         ivs: bytearray, states: bytearray) -> bytes:
    if len(sizes) != len(states) * 4 or len(ivs) != len(states) * 16:
        raise ValueError("Invalid batch metadata lengths")
    out = bytearray()
    offset = 0
    for index in range(len(states)):
        size = struct.unpack_from("<I", sizes, index * 4)[0]
        end = offset + size
        if end > len(data_flat):
            raise ValueError("data_flat too short for given sizes")
        part = data_flat[offset:end]
        iv = bytearray(ivs[index * 16:(index + 1) * 16])
        state = bytearray([states[index]])
        out.extend(_ctr(part, key, iv, state))
        ivs[index * 16:(index + 1) * 16] = iv
        states[index] = state[0]
        offset = end
    if offset > len(data_flat):
        raise ValueError("data_flat too short for given sizes")
    out.extend(data_flat[offset:])
    return bytes(out)


def ctr256_decrypt_batch(data_flat: bytes, sizes: bytearray, key: bytes,
                         ivs: bytearray, states: bytearray) -> bytes:
    return ctr256_encrypt_batch(data_flat, sizes, key, ivs, states)


def kdf(auth_key: bytes, msg_key: bytes, outgoing: bool):
    if len(auth_key) != 256 or len(msg_key) != 16:
        raise ValueError("Invalid auth_key or msg_key length")
    result = _Native.nativeKdf(auth_key, msg_key, outgoing)
    return bytes(result[0]), bytes(result[1])


def pack_message(msg_id: int, seq_no: int, body: bytes, salt: int,
                 session_id: bytes, auth_key: bytes, auth_key_id: bytes) -> bytes:
    if len(session_id) != 8 or len(auth_key) != 256 or len(auth_key_id) != 8:
        raise ValueError("Invalid session or authentication key length")
    return bytes(_Native.nativePackMessage(
        msg_id, seq_no, body, salt, session_id, auth_key, auth_key_id
    ))


def unpack_message(packed: bytes, session_id: bytes, auth_key: bytes, auth_key_id: bytes):
    if len(session_id) != 8 or len(auth_key) != 256 or len(auth_key_id) != 8:
        raise ValueError("Invalid session or authentication key length")
    encoded = bytes(_Native.nativeUnpackMessage(packed, session_id, auth_key, auth_key_id))
    if len(encoded) < 16:
        raise ValueError("Invalid native unpack result")
    msg_id, seq_no, length = struct.unpack_from("<qiI", encoded, 0)
    body = encoded[16:16 + length]
    if len(body) != length:
        raise ValueError("Invalid native unpack body length")
    total_len = len(packed) - 8
    return msg_id, seq_no, length, body, total_len
