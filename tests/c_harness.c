/*
 * c_harness.c - reference implementation of the MEGA crypto helpers,
 * copied verbatim from megatools tools/libtools/oldmega.c.
 *
 * Used only to generate independent verification vectors in tests/vectors.json
 * so the Python port can be regression-tested offline.
 *
 * Build: gcc -o /tmp/opencode/ref c_harness.c -lcrypto
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <openssl/aes.h>
#include <openssl/evp.h>

static void base64urlencode_buf(const unsigned char *data, size_t len, char *out)
{
    size_t j;
    size_t blen = 4 * ((len + 2) / 3) + 1;
    char *b64 = malloc(blen);
    int n = EVP_EncodeBlock((unsigned char *)b64, data, (int)len);
    j = 0;
    for (int i = 0; i < n; i++) {
        if (b64[i] == '=') continue;      /* strip padding, like g_base64_encode+strip */
        out[j++] = (b64[i] == '+') ? '-' : (b64[i] == '/') ? '_' : b64[i];
    }
    out[j++] = '\0';
    free(b64);
}

static void aes128_encrypt_block(unsigned char *out, const unsigned char *in, const unsigned char *key)
{
    AES_KEY k;
    AES_set_encrypt_key(key, 128, &k);
    AES_encrypt(in, out, &k);
}

static void make_password_key_vector(const char *password, unsigned char *pkey)
{
    unsigned char k[16] = {0x93, 0xC4, 0x67, 0xE3, 0x7D, 0xB0, 0xC7, 0xA4,
                           0xD1, 0xBE, 0x3F, 0x81, 0x01, 0x52, 0xCB, 0x56};
    size_t len = strlen(password);
    int r, i;
    for (r = 65536; r--; ) {
        for (i = 0; i < (int)len; i += 16) {
            unsigned char key2[16] = {0}, tmp[16];
            size_t n = (len - i < 16) ? (len - i) : 16;
            memcpy(key2, password + i, n);
            aes128_encrypt_block(tmp, k, key2);
            memcpy(k, tmp, 16);
        }
    }
    memcpy(pkey, k, 16);
}

static void make_username_hash_vector(const char *un, const unsigned char *key, char *out)
{
    unsigned char hash[16] = {0}, tmp[16], oh[8];
    int i, l = (int)strlen(un);
    for (i = 0; i < l; i++) hash[i % 16] ^= (unsigned char)un[i];
    for (i = 16384; i--; ) {
        aes128_encrypt_block(tmp, hash, key);
        memcpy(hash, tmp, 16);
    }
    memcpy(oh, hash, 4);
    memcpy(oh + 4, hash + 8, 4);
    base64urlencode_buf(oh, 8, out);
}

static void b64_aes_encrypt_vector(const unsigned char *data, size_t len, const unsigned char *key, char *out)
{
    unsigned char cipher[64] = {0};
    for (size_t off = 0; off < len; off += 16)
        aes128_encrypt_block(cipher + off, data + off, key);
    base64urlencode_buf(cipher, len, out);
}

int main(void)
{
    unsigned char pk[16], mk[16], cdata[32];
    char b64[256];

    /* 1. password key for a fixed password */
    make_password_key_vector("Tr0ub4dor&3", pk);
    printf("pwkey_1=");
    for (int i = 0; i < 16; i++) printf("%02x", pk[i]);
    printf("\n");

    /* 2. password key for the empty-ish/edge password "a" */
    make_password_key_vector("a", pk);
    printf("pwkey_2=");
    for (int i = 0; i < 16; i++) printf("%02x", pk[i]);
    printf("\n");

    /* 3. username hash of a fixed email given pwkey_1 */
    make_password_key_vector("Tr0ub4dor&3", pk);
    make_username_hash_vector("megamendung.test+mega1@gmail.com", pk, b64);
    printf("uh_1=%s\n", b64);

    /* 4. username hash of a different casing (must match lowercased) */
    make_username_hash_vector("megamendung.test+mega1@GMAIL.COM", pk, b64);
    printf("uh_upper=%s\n", b64);

    /* 5. b64_aes128_encrypt of a fixed master key under pwkey_1 */
    memset(mk, 0xAB, 16);
    b64_aes_encrypt_vector(mk, 16, pk, b64);
    printf("k_enc_1=%s\n", b64);

    /* 6. b64_aes128_encrypt of the 32-byte challenge block under pwkey_1 */
    memset(cdata, 0x11, 32);
    b64_aes_encrypt_vector(cdata, 32, pk, b64);
    printf("c_enc_1=%s\n", b64);

    return 0;
}