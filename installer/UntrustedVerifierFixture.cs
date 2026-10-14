// Test only; must never be used by a shipped installer.
namespace Chaos.NaCl {
    public static class Ed25519 {
        public static bool Verify(byte[] signature, byte[] message, byte[] key) { return true; }
    }
}
