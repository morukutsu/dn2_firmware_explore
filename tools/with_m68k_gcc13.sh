#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FORMULA=m68k-linux-gnu-gcc@13
BINUTILS_FORMULA=m68k-linux-gnu-binutils@2.42

if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is required to install the project-local GCC 13.3 toolchain." >&2
    exit 2
fi

if [ -n "${DNFW_M68K_GCC13_PREFIX:-}" ]; then
    PREFIX=$DNFW_M68K_GCC13_PREFIX
else
    PREFIX=$(brew --prefix "$FORMULA" 2>/dev/null || true)
    BINUTILS_PREFIX=$(brew --prefix "$BINUTILS_FORMULA" 2>/dev/null || true)
    if [ -z "$PREFIX" ] || [ ! -x "$PREFIX/bin/m68k-linux-gnu-gcc" ] || \
       [ -z "$BINUTILS_PREFIX" ] || [ ! -x "$BINUTILS_PREFIX/bin/m68k-linux-gnu-as" ]; then
        TAP=dnfw/local
        TAP_DIR=$(brew --repository)/Library/Taps/dnfw/homebrew-local
        if [ ! -d "$TAP_DIR" ]; then
            brew tap-new "$TAP"
        fi
        mkdir -p "$TAP_DIR/Formula"
        for formula in m68k-linux-gnu-binutils@2.42 m68k-linux-gnu-gcc@13; do
            if ! cmp -s "$ROOT/tools/$formula.rb" "$TAP_DIR/Formula/$formula.rb"; then
                cp "$ROOT/tools/$formula.rb" "$TAP_DIR/Formula/$formula.rb"
            fi
        done
        brew unlink "$FORMULA" >/dev/null 2>&1 || true
        brew unlink "$BINUTILS_FORMULA" >/dev/null 2>&1 || true
        if [ -z "$BINUTILS_PREFIX" ] || [ ! -x "$BINUTILS_PREFIX/bin/m68k-linux-gnu-as" ]; then
            brew install --build-from-source --formula "$TAP/$BINUTILS_FORMULA"
        fi
        if [ -x "$PREFIX/bin/m68k-linux-gnu-gcc" ]; then
            brew reinstall --build-from-source --formula "$TAP/$FORMULA"
        else
            brew install --build-from-source --formula "$TAP/$FORMULA"
        fi
        PREFIX=$(brew --prefix "$FORMULA")
        BINUTILS_PREFIX=$(brew --prefix "$BINUTILS_FORMULA")
    fi
fi

GCC=$PREFIX/bin/m68k-linux-gnu-gcc
if [ ! -x "$GCC" ]; then
    echo "GCC 13.3 was not found at $GCC" >&2
    exit 2
fi

VERSION=$($GCC -dumpfullversion -dumpversion)
case "$VERSION" in
    13.3*) ;;
    *)
        echo "expected GCC 13.3, found $VERSION at $GCC" >&2
        exit 2
        ;;
esac

BINUTILS=${BINUTILS_PREFIX:-$(brew --prefix "$BINUTILS_FORMULA")}/bin
export PATH="$PREFIX/bin:$BINUTILS:$PATH"

if [ "$#" -eq 0 ]; then
    echo "m68k-linux-gnu GCC $VERSION ready at $GCC"
    echo "Run project commands as: $0 <command> [args...]"
    exit 0
fi

exec "$@"
