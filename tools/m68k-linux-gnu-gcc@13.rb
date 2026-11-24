class M68kLinuxGnuGccAT13 < Formula
  desc "GNU compiler collection m68k-linux-gnu, GCC 13.3"
  homepage "https://gcc.gnu.org"
  url "https://ftpmirror.gnu.org/gcc/gcc-13.3.0/gcc-13.3.0.tar.xz"
  mirror "https://ftp.gnu.org/gnu/gcc/gcc-13.3.0/gcc-13.3.0.tar.xz"
  sha256 "0845e9621c9543a13f484e94584a49ffc0129970e9914624235fc1d061a0c083"
  license "GPL-3.0-or-later" => { with: "GCC-exception-3.1" }

  keg_only :versioned_formula

  depends_on "gmp"
  depends_on "isl"
  depends_on "libmpc"
  depends_on "m68k-linux-gnu-binutils@2.42"
  depends_on "mpfr"
  depends_on "zstd"

  on_linux do
    depends_on "zlib-ng-compat"
  end

  def install
    target = "m68k-linux-gnu"
    binutils = formula_opt_bin("m68k-linux-gnu-binutils@2.42")
    toolbin = buildpath / "target-binutils"
    mkdir_p toolbin
    %w[as ld ar nm objcopy objdump ranlib].each do |tool|
      ln_s "#{binutils}/m68k-linux-gnu-#{tool}", toolbin/"m68k-linux-gnu-#{tool}"
    end
    ENV.prepend_path "PATH", toolbin
    mkdir "m68k-linux-gnu-build" do
      system "../configure", "--target=#{target}",
                              "--prefix=#{prefix}",
                              "--infodir=#{info}/#{target}",
                              "--disable-nls",
                              "--disable-threads",
                              "--disable-gcov",
                              "--disable-shared",
                              "--without-headers",
                              "--with-newlib",
                              "--with-as=#{binutils}/m68k-linux-gnu-as",
                              "--with-ld=#{binutils}/m68k-linux-gnu-ld",
                              "--enable-languages=c,c++,objc,lto",
                              "--enable-lto",
                              "--with-system-zlib",
                              "--with-zstd",
                              *std_configure_args
      system "make", "all-gcc"
      system "make", "install-gcc"
      system "make", "all-target-libgcc"
      system "make", "install-target-libgcc"
    end
  end

  test do
    (testpath / "test.c").write <<~C
      int main(void) { return 0; }
    C
    system bin/"m68k-linux-gnu-gcc", "-c", "-o", "test.o", "test.c"
    assert_match "file format elf32-m68k",
                 shell_output("#{bin}/m68k-linux-gnu-objdump -a test.o")
  end
end
