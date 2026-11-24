use jni::objects::{JByteArray, JClass, JObject, JObjectArray};
use jni::sys::{jbyteArray, jint, jlong, jobjectArray};
use jni::JNIEnv;

fn read_bytes(env: &mut JNIEnv<'_>, array: &JByteArray<'_>) -> Result<Vec<u8>, String> {
    env.convert_byte_array(array).map_err(|e| e.to_string())
}

fn write_bytes(env: &mut JNIEnv<'_>, bytes: &[u8]) -> Result<jbyteArray, String> {
    let out = env.new_byte_array(bytes.len() as jint).map_err(|e| e.to_string())?;
    let signed: Vec<i8> = bytes.iter().map(|b| *b as i8).collect();
    env.set_byte_array_region(&out, 0, &signed).map_err(|e| e.to_string())?;
    Ok(out.into_raw())
}

fn fail_bytes(env: &mut JNIEnv<'_>, error: impl ToString) -> jbyteArray {
    let _ = env.throw_new("java/lang/IllegalArgumentException", error.to_string());
    std::ptr::null_mut()
}

fn fail_array(env: &mut JNIEnv<'_>, error: impl ToString) -> jobjectArray {
    let _ = env.throw_new("java/lang/IllegalArgumentException", error.to_string());
    std::ptr::null_mut()
}

fn fixed<const N: usize>(value: Vec<u8>, label: &str) -> Result<[u8; N], String> {
    value.try_into().map_err(|v: Vec<u8>| format!("{label} must be {N} bytes, got {}", v.len()))
}

#[no_mangle]
pub extern "system" fn Java_com_soebb_instabulk_crypto_WarpCrypto_nativeIgeEncrypt(
    mut env: JNIEnv<'_>, _class: JClass<'_>, data: JByteArray<'_>, key: JByteArray<'_>, iv: JByteArray<'_>,
) -> jbyteArray {
    let result = (|| {
        let mut data = read_bytes(&mut env, &data)?;
        let key = fixed::<32>(read_bytes(&mut env, &key)?, "key")?;
        let iv = fixed::<32>(read_bytes(&mut env, &iv)?, "iv")?;
        warpcrypto::ige256_encrypt(&mut data, &key, &iv).map_err(|e| e.to_string())?;
        write_bytes(&mut env, &data)
    })();
    result.unwrap_or_else(|e| fail_bytes(&mut env, e))
}

#[no_mangle]
pub extern "system" fn Java_com_soebb_instabulk_crypto_WarpCrypto_nativeIgeDecrypt(
    mut env: JNIEnv<'_>, _class: JClass<'_>, data: JByteArray<'_>, key: JByteArray<'_>, iv: JByteArray<'_>,
) -> jbyteArray {
    let result = (|| {
        let mut data = read_bytes(&mut env, &data)?;
        let key = fixed::<32>(read_bytes(&mut env, &key)?, "key")?;
        let iv = fixed::<32>(read_bytes(&mut env, &iv)?, "iv")?;
        warpcrypto::ige256_decrypt(&mut data, &key, &iv).map_err(|e| e.to_string())?;
        write_bytes(&mut env, &data)
    })();
    result.unwrap_or_else(|e| fail_bytes(&mut env, e))
}

#[no_mangle]
pub extern "system" fn Java_com_soebb_instabulk_crypto_WarpCrypto_nativeCtr(
    mut env: JNIEnv<'_>, _class: JClass<'_>, data: JByteArray<'_>, key: JByteArray<'_>,
    iv: JByteArray<'_>, state: JByteArray<'_>,
) -> jobjectArray {
    let result = (|| {
        let mut data = read_bytes(&mut env, &data)?;
        let key = fixed::<32>(read_bytes(&mut env, &key)?, "key")?;
        let mut iv_bytes = fixed::<16>(read_bytes(&mut env, &iv)?, "iv")?;
        let state_bytes = read_bytes(&mut env, &state)?;
        if state_bytes.len() != 1 { return Err("state must be a one-byte array".into()); }
        let mut state_value = state_bytes[0];
        warpcrypto::ctr256(&mut data, &key, &mut iv_bytes, &mut state_value);

        let byte_array_class = env.find_class("[B").map_err(|e| e.to_string())?;
        let array: JObjectArray = env.new_object_array(3, byte_array_class, JObject::null())
            .map_err(|e| e.to_string())?;
        let data_obj = JObject::from(env.byte_array_from_slice(&data).map_err(|e| e.to_string())?);
        let iv_obj = JObject::from(env.byte_array_from_slice(&iv_bytes).map_err(|e| e.to_string())?);
        let state_obj = JObject::from(env.byte_array_from_slice(&[state_value]).map_err(|e| e.to_string())?);
        env.set_object_array_element(&array, 0, data_obj).map_err(|e| e.to_string())?;
        env.set_object_array_element(&array, 1, iv_obj).map_err(|e| e.to_string())?;
        env.set_object_array_element(&array, 2, state_obj).map_err(|e| e.to_string())?;
        Ok(array.into_raw())
    })();
    result.unwrap_or_else(|e: String| fail_array(&mut env, e))
}

#[no_mangle]
pub extern "system" fn Java_com_soebb_instabulk_crypto_WarpCrypto_nativeKdf(
    mut env: JNIEnv<'_>, _class: JClass<'_>, auth_key: JByteArray<'_>, msg_key: JByteArray<'_>, outgoing: jni::sys::jboolean,
) -> jobjectArray {
    let result = (|| {
        let auth_key = fixed::<256>(read_bytes(&mut env, &auth_key)?, "auth_key")?;
        let msg_key = fixed::<16>(read_bytes(&mut env, &msg_key)?, "msg_key")?;
        let (key, iv) = warpcrypto::kdf(&auth_key, &msg_key, outgoing != 0);
        let byte_array_class = env.find_class("[B").map_err(|e| e.to_string())?;
        let array: JObjectArray = env.new_object_array(2, byte_array_class, JObject::null()).map_err(|e| e.to_string())?;
        let key_obj = JObject::from(env.byte_array_from_slice(&key).map_err(|e| e.to_string())?);
        let iv_obj = JObject::from(env.byte_array_from_slice(&iv).map_err(|e| e.to_string())?);
        env.set_object_array_element(&array, 0, key_obj).map_err(|e| e.to_string())?;
        env.set_object_array_element(&array, 1, iv_obj).map_err(|e| e.to_string())?;
        Ok(array.into_raw())
    })();
    result.unwrap_or_else(|e: String| fail_array(&mut env, e))
}

#[no_mangle]
pub extern "system" fn Java_com_soebb_instabulk_crypto_WarpCrypto_nativePackMessage(
    mut env: JNIEnv<'_>, _class: JClass<'_>, msg_id: jlong, seq_no: jint, body: JByteArray<'_>,
    salt: jlong, session_id: JByteArray<'_>, auth_key: JByteArray<'_>, auth_key_id: JByteArray<'_>,
) -> jbyteArray {
    let result = (|| {
        let body = read_bytes(&mut env, &body)?;
        let session_id = fixed::<8>(read_bytes(&mut env, &session_id)?, "session_id")?;
        let auth_key = fixed::<256>(read_bytes(&mut env, &auth_key)?, "auth_key")?;
        let auth_key_id = fixed::<8>(read_bytes(&mut env, &auth_key_id)?, "auth_key_id")?;
        let packed = warpcrypto::pack_message(&auth_key, &auth_key_id, &session_id, salt, msg_id, seq_no, &body);
        write_bytes(&mut env, &packed)
    })();
    result.unwrap_or_else(|e| fail_bytes(&mut env, e))
}

#[no_mangle]
pub extern "system" fn Java_com_soebb_instabulk_crypto_WarpCrypto_nativeUnpackMessage(
    mut env: JNIEnv<'_>, _class: JClass<'_>, packed: JByteArray<'_>, session_id: JByteArray<'_>,
    auth_key: JByteArray<'_>, auth_key_id: JByteArray<'_>, incoming: jni::sys::jboolean,
) -> jbyteArray {
    let result = (|| {
        let packed = read_bytes(&mut env, &packed)?;
        let session_id = fixed::<8>(read_bytes(&mut env, &session_id)?, "session_id")?;
        let auth_key = fixed::<256>(read_bytes(&mut env, &auth_key)?, "auth_key")?;
        let auth_key_id = fixed::<8>(read_bytes(&mut env, &auth_key_id)?, "auth_key_id")?;
        let message = warpcrypto::unpack_message(&packed, &auth_key, &auth_key_id, &session_id, incoming != 0)
            .map_err(|e| e.to_string())?;
        let mut encoded = Vec::with_capacity(16 + message.body.len());
        encoded.extend_from_slice(&message.msg_id.to_le_bytes());
        encoded.extend_from_slice(&message.seq_no.to_le_bytes());
        encoded.extend_from_slice(&(message.body.len() as u32).to_le_bytes());
        encoded.extend_from_slice(&message.body);
        write_bytes(&mut env, &encoded)
    })();
    result.unwrap_or_else(|e| fail_bytes(&mut env, e))
}
