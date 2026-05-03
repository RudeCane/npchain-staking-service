NPCHAIN_SRC      := $(realpath ../../npchain-source)
DILITHIUM_VENDOR := $(NPCHAIN_SRC)/vendor/dilithium-ref
INCLUDE_DIR      := $(NPCHAIN_SRC)/include
SRC_DIR          := $(NPCHAIN_SRC)/src

CXX      := g++
CC       := gcc
CXXFLAGS := -std=c++20 -O2 -fPIC -Wno-unused-function
CFLAGS   := -O2 -fPIC -w -DDILITHIUM_MODE=3
INCLUDES := -I $(INCLUDE_DIR) -I $(SRC_DIR) -I $(DILITHIUM_VENDOR)
LDFLAGS  := -shared

VENDOR_C_SRCS := \
    $(DILITHIUM_VENDOR)/sign.c \
    $(DILITHIUM_VENDOR)/packing.c \
    $(DILITHIUM_VENDOR)/poly.c \
    $(DILITHIUM_VENDOR)/polyvec.c \
    $(DILITHIUM_VENDOR)/ntt.c \
    $(DILITHIUM_VENDOR)/reduce.c \
    $(DILITHIUM_VENDOR)/rounding.c \
    $(DILITHIUM_VENDOR)/fips202.c \
    $(DILITHIUM_VENDOR)/symmetric-shake.c \
    $(DILITHIUM_VENDOR)/randombytes.c

VENDOR_OBJS := $(patsubst %.c,build/%.o,$(notdir $(VENDOR_C_SRCS)))
CPP_OBJS    := build/dilithium3_capi.o build/dilithium3.o build/hash.o build/ml_dsa_verify.o

TARGET := libnpc_dilithium3.so

.PHONY: all clean version

all: $(TARGET) version

$(TARGET): $(VENDOR_OBJS) $(CPP_OBJS)
	$(CXX) $(LDFLAGS) -o $@ $^
	@echo "Built $(TARGET)"

build:
	@mkdir -p build

build/%.o: $(DILITHIUM_VENDOR)/%.c | build
	$(CC) $(CFLAGS) $(INCLUDES) -c -o $@ $<

build/dilithium3_capi.o: dilithium3_capi.cpp | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c -o $@ $<

build/dilithium3.o: $(SRC_DIR)/crypto/dilithium3.cpp | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c -o $@ $<

build/hash.o: $(SRC_DIR)/crypto/hash.cpp | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c -o $@ $<

version:
	@cd $(NPCHAIN_SRC) && git rev-parse HEAD > $(CURDIR)/VERSION
	@echo "Pinned to commit $$(cat VERSION)"

clean:
	rm -rf build $(TARGET) VERSION


build/ml_dsa_verify.o: $(DILITHIUM_VENDOR)/ml_dsa_verify.cpp | build
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c -o $@ $<
