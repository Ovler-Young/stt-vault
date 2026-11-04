package media

import (
	"os"
	"testing"
)

func FuzzWAVArithmetic(f *testing.F) {
	f.Add([]byte("RIFF"))
	f.Add(canonicalWAV(1))
	badSize := canonicalWAV(1)
	badSize[4] = 0
	f.Add(badSize)
	f.Fuzz(func(t *testing.T, input []byte) {
		file, err := os.CreateTemp("", "wav-fuzz")
		if err != nil {
			t.Fatal(err)
		}
		defer os.Remove(file.Name())
		if _, err = file.Write(input); err == nil {
			_ = file.Close()
			_, _ = ValidateWAV(file.Name(), 120)
		}
	})
}
