package modelscan

import (
	"bufio"
	"encoding/binary"
	"errors"
	"io"
	"os"
	"regexp"
	"strings"
)

// Helper files are GGUFs that only work added onto a main model, never on
// their own: a vision/audio projector (--mmproj) and an MTP head (a model's
// multi-token-prediction layers split into their own small file, added as a
// draft model). They are not models, so the scan leaves them out; the
// Builder pairs them with their model (builder/src/library.py).

// mtpHeadMaxLayers is the most layers an MTP head has: one to a few
// prediction layers. A full model with MTP built in has dozens.
const mtpHeadMaxLayers = 4

var mtpName = regexp.MustCompile(`(?i)(^|[-_.])(mtp|nextn)($|[-_.])`)

// IsHelperFile reports whether path is a projector or an MTP head rather
// than a model of its own. base is the file name without its extension.
func IsHelperFile(path, base string) bool {
	lower := strings.ToLower(base)
	if strings.Contains(lower, "mmproj") {
		return true
	}
	arch, layers, err := readGGUFArch(path)
	if err != nil {
		return false
	}
	switch arch {
	case "clip", "mmproj": // llama.cpp's projector files, whatever their name
		return true
	}
	return mtpName.MatchString(base) && layers > 0 && layers <= mtpHeadMaxLayers
}

var errNotGGUF = errors.New("not a GGUF file")

// readGGUFArch reads general.architecture and <arch>.block_count from a GGUF
// header. Only the metadata is read, never the tensors.
func readGGUFArch(path string) (arch string, layers int, err error) {
	f, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer f.Close()
	r := &ggufReader{r: bufio.NewReaderSize(f, 64<<10)}
	if magic := r.bytes(4); string(magic) != "GGUF" {
		return "", 0, errNotGGUF
	}
	if v := r.u32(); v < 2 {
		return "", 0, errNotGGUF
	}
	r.u64() // tensor count
	n := r.u64()
	if r.err != nil {
		return "", 0, r.err
	}
	var counts = map[string]int{}
	for i := uint64(0); i < n && i < 100000; i++ {
		key := r.str()
		vtype := r.u32()
		if r.err != nil {
			return "", 0, r.err
		}
		switch {
		case key == "general.architecture" && vtype == 8:
			arch = r.str()
		case strings.HasSuffix(key, ".block_count"):
			counts[strings.TrimSuffix(key, ".block_count")] = int(r.intValue(vtype))
		case strings.HasPrefix(key, "tokenizer."):
			// the architecture keys come first; the tokenizer's arrays are huge
			return arch, counts[arch], nil
		default:
			r.skip(vtype)
		}
		if r.err != nil {
			return "", 0, r.err
		}
	}
	return arch, counts[arch], nil
}

type ggufReader struct {
	r   *bufio.Reader
	err error
}

func (g *ggufReader) bytes(n int) []byte {
	if g.err != nil {
		return nil
	}
	b := make([]byte, n)
	_, g.err = io.ReadFull(g.r, b)
	return b
}

func (g *ggufReader) discard(n uint64) {
	if g.err != nil {
		return
	}
	_, g.err = g.r.Discard(int(n))
}

func (g *ggufReader) u32() uint32 {
	b := g.bytes(4)
	if g.err != nil {
		return 0
	}
	return binary.LittleEndian.Uint32(b)
}

func (g *ggufReader) u64() uint64 {
	b := g.bytes(8)
	if g.err != nil {
		return 0
	}
	return binary.LittleEndian.Uint64(b)
}

func (g *ggufReader) str() string {
	n := g.u64()
	if g.err == nil && n > 1<<20 {
		g.err = errNotGGUF
	}
	return string(g.bytes(int(n)))
}

// scalarSize is the size of each GGUF scalar value type (string = 8, array = 9).
var scalarSize = map[uint32]uint64{0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

func (g *ggufReader) intValue(vtype uint32) uint64 {
	switch vtype {
	case 0, 1, 7:
		b := g.bytes(1)
		if g.err == nil {
			return uint64(b[0])
		}
	case 2, 3:
		b := g.bytes(2)
		if g.err == nil {
			return uint64(binary.LittleEndian.Uint16(b))
		}
	case 4, 5:
		return uint64(g.u32())
	case 10, 11:
		return g.u64()
	default:
		g.skip(vtype)
	}
	return 0
}

func (g *ggufReader) skip(vtype uint32) {
	switch vtype {
	case 8:
		g.discard(g.u64())
	case 9:
		elem, n := g.u32(), g.u64()
		if size, ok := scalarSize[elem]; ok {
			g.discard(size * n)
			return
		}
		for i := uint64(0); i < n && g.err == nil; i++ {
			g.skip(elem)
		}
	default:
		size, ok := scalarSize[vtype]
		if !ok {
			g.err = errNotGGUF
			return
		}
		g.discard(size)
	}
}
