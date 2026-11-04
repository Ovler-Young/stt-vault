package media

import (
	"encoding/binary"
	"os"
	"testing"
)

func canonicalWAV(frames int) []byte {
	size := 36 + frames*2
	b := make([]byte, 44+frames*2)
	copy(b, "RIFF")
	binary.LittleEndian.PutUint32(b[4:], uint32(size))
	copy(b[8:], "WAVEfmt ")
	binary.LittleEndian.PutUint32(b[16:], 16)
	binary.LittleEndian.PutUint16(b[20:], 1)
	binary.LittleEndian.PutUint16(b[22:], 1)
	binary.LittleEndian.PutUint32(b[24:], 16000)
	binary.LittleEndian.PutUint32(b[28:], 32000)
	binary.LittleEndian.PutUint16(b[32:], 2)
	binary.LittleEndian.PutUint16(b[34:], 16)
	copy(b[36:], "data")
	binary.LittleEndian.PutUint32(b[40:], uint32(frames*2))
	return b
}
func TestValidateWAV(t *testing.T) {
	f, err := os.CreateTemp("", "wav-test")
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(canonicalWAV(800)); err != nil {
		t.Fatal(err)
	}
	if err = f.Close(); err != nil {
		t.Fatal(err)
	}
	duration, err := ValidateWAV(f.Name(), 1)
	if err != nil || duration != .05 {
		t.Fatalf("duration %v err %v", duration, err)
	}
}

func TestValidateWAVRejectsInvalidFramingAndPCMFields(t *testing.T) {
	cases := map[string]func([]byte) []byte{
		"trailing byte":       func(b []byte) []byte { return append(b, 0) },
		"bad declared size":   func(b []byte) []byte { binary.LittleEndian.PutUint32(b[4:], 36); return b },
		"bad block alignment": func(b []byte) []byte { binary.LittleEndian.PutUint16(b[32:], 4); return b },
		"bad byte rate":       func(b []byte) []byte { binary.LittleEndian.PutUint32(b[28:], 16000); return b },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			b := canonicalWAV(800)
			b = mutate(b)
			path := t.TempDir() + "/invalid.wav"
			if err := os.WriteFile(path, b, 0600); err != nil {
				t.Fatal(err)
			}
			if _, err := ValidateWAV(path, 1); err == nil {
				t.Fatal("accepted invalid WAV")
			}
		})
	}
}
