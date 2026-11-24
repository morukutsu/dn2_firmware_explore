class M68kLinuxGnuBinutilsAT242 < Formula
  desc "GNU binary tools for m68k-linux-gnu, binutils 2.42"
  homepage "https://www.gnu.org/software/binutils/"
  url "https://ftpmirror.gnu.org/binutils/binutils-2.42.tar.xz"
  mirror "https://ftp.gnu.org/gnu/binutils/binutils-2.42.tar.xz"
  sha256 "f6e4d41fd5fc778b06b7891457b3620da5ecea1006c6a4a41ae998109f85a800"
  license "GPL-3.0-or-later"

  keg_only :versioned_formula

  def install
    mkdir "build" do
      system "../configure", "--target=m68k-linux-gnu",
                              "--prefix=#{prefix}",
                              "--disable-nls",
                              "--disable-werror",
                              "--disable-gdb",
                              "--disable-gdbserver",
                              "--disable-sim",
                              "--disable-gprofng",
                              "--with-system-zlib",
                              *std_configure_args
      system "make", "MAKEINFO=true"
      system "make", "MAKEINFO=true", "install"
    end
  end

  test do
    (testpath / "test.s").write <<~ASM
      nop
    ASM
    system bin/"m68k-linux-gnu-as", "-o", "test.o", "test.s"
    assert_match "elf32-m68k",
                 shell_output("#{bin}/m68k-linux-gnu-objdump -a test.o")
  end
end
