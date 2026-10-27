// Package gpumem reads per-card VRAM sizes so a router can place models on
// individual GPUs.
package gpumem

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Card is one GPU. ID is the string HIP_VISIBLE_DEVICES takes: ROCm's GPU
// index when rocm-smi answered, otherwise the card's position among the DRM
// cards that have VRAM (see FromSysfs).
type Card struct {
	ID       string
	TotalGiB float64
}

// SysfsRoot is the kernel DRM root. A var so tests can point it elsewhere.
var SysfsRoot = "/sys/class/drm"

// ROCmSMIArgs is the rocm-smi call used to read VRAM. A var for tests.
var ROCmSMIArgs = []string{"rocm-smi", "--showmeminfo", "vram"}

const gib = 1 << 30

// Detect returns the installed cards ordered by numeric ID. rocm-smi is
// preferred because its indices are the ones HIP_VISIBLE_DEVICES uses; the
// sysfs fallback numbers the cards 0..n-1 in DRM order, HIP's usual order.
// source reports where the cards came from, "rocm-smi" or "sysfs"; both are
// empty when nothing could be read.
func Detect() (cards []Card, source string) {
	if cards := fromROCmSMI(); len(cards) > 0 {
		return cards, "rocm-smi"
	}
	if cards := FromSysfs(SysfsRoot); len(cards) > 0 {
		return cards, "sysfs"
	}
	return nil, ""
}

func fromROCmSMI() []Card {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, ROCmSMIArgs[0], ROCmSMIArgs[1:]...).Output()
	if err != nil {
		return nil
	}
	return ParseROCmSMI(string(out))
}

// rocmTotalLine matches `GPU[1] : VRAM Total Memory (B): 34342961152`.
var rocmTotalLine = regexp.MustCompile(`^GPU\[(\d+)\]\s*:\s*(.+?):\s*(\d+)\s*$`)

// ParseROCmSMI reads `rocm-smi --showmeminfo vram` output into cards.
func ParseROCmSMI(text string) []Card {
	var cards []Card
	for _, line := range strings.Split(text, "\n") {
		m := rocmTotalLine.FindStringSubmatch(strings.TrimSpace(line))
		if m == nil {
			continue
		}
		label := strings.ToLower(m[2])
		// "VRAM Total Used Memory" also contains "total"; skip the used line.
		if !strings.Contains(label, "total") || strings.Contains(label, "used") {
			continue
		}
		bytes, err := strconv.ParseInt(m[3], 10, 64)
		if err != nil || bytes <= 0 {
			continue
		}
		cards = append(cards, Card{ID: m[1], TotalGiB: float64(bytes) / gib})
	}
	sortCards(cards)
	return cards
}

var cardDir = regexp.MustCompile(`^card(\d+)$`)

// FromSysfs reads mem_info_vram_total for every DRM card that reports it.
//
// The cards are numbered 0, 1, ... in DRM order, not by their DRM number. HIP
// numbers devices from 0 in that same order, while DRM numbers often start at
// 1 (card0 is a display or simply absent): with two MI50s at card1 and card2
// the DRM numbers sent models to HIP device 1 and a device 2 that does not
// exist.
func FromSysfs(root string) []Card {
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	var cards []Card
	for _, e := range entries {
		m := cardDir.FindStringSubmatch(e.Name())
		if m == nil {
			continue
		}
		data, err := os.ReadFile(filepath.Join(root, e.Name(), "device", "mem_info_vram_total"))
		if err != nil {
			continue
		}
		bytes, err := strconv.ParseInt(strings.TrimSpace(string(data)), 10, 64)
		if err != nil || bytes <= 0 {
			continue
		}
		cards = append(cards, Card{ID: m[1], TotalGiB: float64(bytes) / gib})
	}
	sortCards(cards)
	for i := range cards {
		cards[i].ID = strconv.Itoa(i)
	}
	return cards
}

func sortCards(cards []Card) {
	sort.Slice(cards, func(i, j int) bool {
		a, _ := strconv.Atoi(cards[i].ID)
		b, _ := strconv.Atoi(cards[j].ID)
		return a < b
	})
}

// Used returns the VRAM in use on each card now, in GiB, keyed like Detect's
// IDs: rocm-smi's when it answers, else the sysfs cards numbered 0..n-1. It
// counts everything on the card, whoever holds it. nil when nothing could be
// read.
func Used() map[string]float64 {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if out, err := exec.CommandContext(ctx, ROCmSMIArgs[0], ROCmSMIArgs[1:]...).Output(); err == nil {
		if used := ParseROCmSMIUsed(string(out)); len(used) > 0 {
			return used
		}
	}
	return UsedFromSysfs(SysfsRoot)
}

// ParseROCmSMIUsed reads the `VRAM Total Used Memory (B)` lines.
func ParseROCmSMIUsed(text string) map[string]float64 {
	out := map[string]float64{}
	for _, line := range strings.Split(text, "\n") {
		m := rocmTotalLine.FindStringSubmatch(strings.TrimSpace(line))
		if m == nil || !strings.Contains(strings.ToLower(m[2]), "used") {
			continue
		}
		if bytes, err := strconv.ParseInt(m[3], 10, 64); err == nil && bytes >= 0 {
			out[m[1]] = float64(bytes) / gib
		}
	}
	return out
}

// UsedFromSysfs reads mem_info_vram_used for the cards FromSysfs lists, in the
// same 0..n-1 numbering.
func UsedFromSysfs(root string) map[string]float64 {
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	type card struct {
		n    int
		used float64
	}
	var cards []card
	for _, e := range entries {
		m := cardDir.FindStringSubmatch(e.Name())
		if m == nil {
			continue
		}
		dev := filepath.Join(root, e.Name(), "device")
		if _, err := os.Stat(filepath.Join(dev, "mem_info_vram_total")); err != nil {
			continue // no VRAM: not one of FromSysfs's cards
		}
		data, err := os.ReadFile(filepath.Join(dev, "mem_info_vram_used"))
		if err != nil {
			continue
		}
		bytes, err := strconv.ParseInt(strings.TrimSpace(string(data)), 10, 64)
		if err != nil || bytes < 0 {
			continue
		}
		n, _ := strconv.Atoi(m[1])
		cards = append(cards, card{n, float64(bytes) / gib})
	}
	sort.Slice(cards, func(i, j int) bool { return cards[i].n < cards[j].n })
	out := make(map[string]float64, len(cards))
	for i, c := range cards {
		out[strconv.Itoa(i)] = c.used
	}
	return out
}
