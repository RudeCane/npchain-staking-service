// pool_keygen.cpp - generate a vendor-format keypair into a binary file.
// Output format: [pk (1952 bytes) | sk (4032 bytes)] = 5984 bytes raw.
// Matches the format used by the original npchain_keygen.exe so the existing
// encrypt-and-deploy flow still works.

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <sys/stat.h>

extern "C" {
    int npc_dil3_keygen(uint8_t* pk, size_t pk_len, uint8_t* sk, size_t sk_len);
    int npc_dil3_derive_address(const uint8_t* pk, size_t pk_len,
                               char* out_addr, size_t out_len);
}

static constexpr size_t PK_SIZE = 1952;
static constexpr size_t SK_SIZE = 4032;

int main(int argc, char** argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <outfile>\n", argv[0]);
        return 1;
    }
    const char* path = argv[1];

    // refuse to overwrite
    FILE* chk = fopen(path, "rb");
    if (chk) {
        fclose(chk);
        fprintf(stderr, "ERROR: %s already exists\n", path);
        return 1;
    }

    uint8_t pk[PK_SIZE], sk[SK_SIZE];
    if (!npc_dil3_keygen(pk, PK_SIZE, sk, SK_SIZE)) {
        fprintf(stderr, "ERROR: keygen failed\n");
        return 1;
    }

    char addr[48] = {0};
    if (!npc_dil3_derive_address(pk, PK_SIZE, addr, sizeof(addr))) {
        fprintf(stderr, "ERROR: derive_address failed\n");
        return 1;
    }

    FILE* f = fopen(path, "wb");
    if (!f) {
        fprintf(stderr, "ERROR: cannot open %s for writing\n", path);
        return 1;
    }
    fwrite(pk, 1, PK_SIZE, f);
    fwrite(sk, 1, SK_SIZE, f);
    fclose(f);
    chmod(path, 0600);

    printf("Address: %s\n", addr);
    printf("Keyfile: %s (5984 bytes)\n", path);
    return 0;
}
