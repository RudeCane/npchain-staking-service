// dilithium3_capi.cpp
// Plain-C ABI wrapper for ctypes. All crypto goes through the pq-crystals
// reference impl (FIPS 204 ML-DSA-65), identical to the wallet's PATH and
// what the L1 verifier accepts.

#include "crypto/hash.hpp"
#include <cstring>
#include <cstdint>
#include <cstddef>

// Vendor sizes (FIPS 204 ML-DSA-65)
static constexpr size_t PK_SIZE  = 1952;
static constexpr size_t SK_SIZE  = 4032;
static constexpr size_t SIG_SIZE = 3309;

extern "C" {
    int pqcrystals_dilithium3_ref_keypair(uint8_t* pk, uint8_t* sk);
    int pqcrystals_dilithium3_ref_signature(
        uint8_t* sig, size_t* siglen,
        const uint8_t* m, size_t mlen,
        const uint8_t* ctx, size_t ctxlen,
        const uint8_t* sk);
}

namespace npchain { namespace ml_dsa {
    bool verify(const uint8_t* sig, size_t sig_len,
                const uint8_t* msg, size_t msg_len,
                const uint8_t* pk,  size_t pk_len);
}}

extern "C" {

size_t npc_dil3_pk_size(void) { return PK_SIZE; }
size_t npc_dil3_sk_size(void) { return SK_SIZE; }
size_t npc_dil3_sig_size(void) { return SIG_SIZE; }

// Generate a fresh keypair in vendor/FIPS 204 format.
// Caller must provide pk[PK_SIZE] and sk[SK_SIZE]. Returns 1 on success.
int npc_dil3_keygen(uint8_t* out_pk, size_t pk_len,
                    uint8_t* out_sk, size_t sk_len) {
    if (pk_len != PK_SIZE) return 0;
    if (sk_len != SK_SIZE) return 0;
    return pqcrystals_dilithium3_ref_keypair(out_pk, out_sk) == 0 ? 1 : 0;
}

int npc_dil3_derive_address(const uint8_t* pk, size_t pk_len,
                            char* out_addr, size_t out_len) {
    if (pk_len != PK_SIZE) return 0;
    if (out_len < 44) return 0;
    auto h = npchain::crypto::sha3_256(npchain::ByteSpan{pk, pk_len});
    out_addr[0] = 'N';
    out_addr[1] = 'P';
    out_addr[2] = 'C';
    const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 20; ++i) {
        out_addr[3 + i*2]     = hex[h[i] >> 4];
        out_addr[3 + i*2 + 1] = hex[h[i] & 0xF];
    }
    out_addr[43] = '\0';
    return 1;
}

// Sign via vendor FIPS 204. Context always empty (matches wallet + L1 verifier).
int npc_dil3_sign(const uint8_t* msg, size_t msg_len,
                  const uint8_t* pk, size_t pk_len,
                  const uint8_t* sk, size_t sk_len,
                  uint8_t* out_sig, size_t* out_sig_len) {
    if (pk_len != PK_SIZE) return 0;
    if (sk_len != SK_SIZE) return 0;
    if (*out_sig_len < SIG_SIZE) return 0;
    (void)pk;
    size_t siglen = *out_sig_len;
    int rc = pqcrystals_dilithium3_ref_signature(
        out_sig, &siglen,
        msg, msg_len,
        nullptr, 0,
        sk);
    if (rc != 0) return 0;
    *out_sig_len = siglen;
    return 1;
}

int npc_dil3_verify(const uint8_t* msg, size_t msg_len,
                    const uint8_t* sig, size_t sig_len,
                    const uint8_t* pk, size_t pk_len) {
    return npchain::ml_dsa::verify(sig, sig_len, msg, msg_len, pk, pk_len) ? 1 : 0;
}

// Smoke test: vendor keygen -> vendor sign -> vendor verify. 1 = pass.
int npc_dil3_self_test(void) {
    uint8_t pk[PK_SIZE], sk[SK_SIZE];
    if (!npc_dil3_keygen(pk, PK_SIZE, sk, SK_SIZE)) return 0;
    const uint8_t msg[] = "npchain self test";
    uint8_t sig[SIG_SIZE];
    size_t siglen = sizeof(sig);
    if (!npc_dil3_sign(msg, sizeof(msg)-1,
                   pk, PK_SIZE, sk, SK_SIZE,
                   sig, &siglen)) return 0;
    return npc_dil3_verify(msg, sizeof(msg)-1, sig, siglen, pk, PK_SIZE);
}

} // extern "C"
