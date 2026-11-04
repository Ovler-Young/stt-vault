package media

import (
	"encoding/binary"
	"fmt"
	"io"
	"os"
)

const MaxRequestPart = 1 << 20

func ValidateWAV(path string, maxSeconds float64) (float64, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	head := make([]byte, 12)
	if _, err = io.ReadFull(f, head); err != nil || string(head[:4]) != "RIFF" || string(head[8:]) != "WAVE" {
		return 0, fmt.Errorf("audio must be canonical WAV")
	}
	info, err := f.Stat()
	if err != nil || info.Size() < 12 || info.Size() > int64(^uint32(0))+8 {
		return 0, fmt.Errorf("audio must be canonical WAV")
	}
	declared := int64(binary.LittleEndian.Uint32(head[4:])) + 8
	if declared != info.Size() {
		return 0, fmt.Errorf("invalid WAV framing")
	}
	var channels, bits, blockAlign uint16
	var rate, byteRate uint32
	var data uint64
	haveFmt := false
	haveData := false
	for offset := int64(12); offset < declared; {
		if declared-offset < 8 {
			return 0, fmt.Errorf("invalid WAV framing")
		}
		chunk := make([]byte, 8)
		if _, err = io.ReadFull(f, chunk); err != nil {
			return 0, fmt.Errorf("invalid WAV framing")
		}
		size := binary.LittleEndian.Uint32(chunk[4:])
		padded := uint64(size) + uint64(size&1)
		if padded < uint64(size) || padded > uint64(declared-offset-8) {
			return 0, fmt.Errorf("invalid WAV framing")
		}
		if size > 25*1024*1024 {
			return 0, fmt.Errorf("audio exceeds configured size")
		}
		if string(chunk[:4]) == "data" {
			if !haveFmt || haveData {
				return 0, fmt.Errorf("invalid WAV framing")
			}
			if _, err = io.CopyN(io.Discard, f, int64(size)); err != nil {
				return 0, fmt.Errorf("invalid WAV")
			}
			data = uint64(size)
			haveData = true
			if size%2 == 1 {
				if _, err = io.CopyN(io.Discard, f, 1); err != nil {
					return 0, fmt.Errorf("invalid WAV")
				}
			}
		} else {
			body := make([]byte, size)
			if _, err = io.ReadFull(f, body); err != nil {
				return 0, fmt.Errorf("invalid WAV")
			}
			switch string(chunk[:4]) {
			case "fmt ":
				if haveFmt || len(body) < 16 {
					return 0, fmt.Errorf("invalid WAV")
				}
				if binary.LittleEndian.Uint16(body[0:2]) != 1 {
					return 0, fmt.Errorf("audio must be PCM16 mono WAV")
				}
				channels = binary.LittleEndian.Uint16(body[2:4])
				rate = binary.LittleEndian.Uint32(body[4:8])
				byteRate = binary.LittleEndian.Uint32(body[8:12])
				blockAlign = binary.LittleEndian.Uint16(body[12:14])
				bits = binary.LittleEndian.Uint16(body[14:16])
				haveFmt = true
			}
			if size%2 == 1 {
				if _, err = io.CopyN(io.Discard, f, 1); err != nil {
					return 0, fmt.Errorf("invalid WAV")
				}
			}
		}
		offset += 8 + int64(padded)
	}
	if !haveFmt || !haveData || channels != 1 || rate != 16000 || bits != 16 || blockAlign != 2 || byteRate != 32000 || data%2 != 0 {
		return 0, fmt.Errorf("audio must be PCM16 mono 16 kHz WAV")
	}
	duration := float64(data/2) / 16000
	if duration > maxSeconds {
		return 0, ErrDuration
	}
	return duration, nil
}

var ErrDuration = fmt.Errorf("audio exceeds the configured duration limit")
