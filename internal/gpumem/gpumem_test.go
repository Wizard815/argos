package gpumem

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGPUMem_ParseROCmSMI(t *testing.T) {
	text := `
GPU[0]		: VRAM Total Memory (B): 34342961152
GPU[0]		: VRAM Total Used Memory (B): 536870912
GPU[1]		: VRAM Total Memory (B): 34342961152
GPU[1]		: VRAM Total Used Memory (B): 0
`
	cards := ParseROCmSMI(text)
	require.Len(t, cards, 2)
	assert.Equal(t, "0", cards[0].ID)
	assert.InDelta(t, 31.98, cards[0].TotalGiB, 0.01)
	assert.Equal(t, "1", cards[1].ID)
}

func TestGPUMem_FromSysfs(t *testing.T) {
	root := t.TempDir()
	for _, card := range []string{"card10", "card2"} {
		dir := filepath.Join(root, card, "device")
		require.NoError(t, os.MkdirAll(dir, 0o755))
		require.NoError(t, os.WriteFile(filepath.Join(dir, "mem_info_vram_total"), []byte("34342961152\n"), 0o644))
	}
	require.NoError(t, os.MkdirAll(filepath.Join(root, "card2-DP-1", "device"), 0o755))
	// A card with no VRAM counter (an iGPU) is skipped.
	require.NoError(t, os.MkdirAll(filepath.Join(root, "card3", "device"), 0o755))

	cards := FromSysfs(root)
	require.Len(t, cards, 2)
	// card2 then card10 (numeric order, not string order), renumbered from 0 as HIP does
	assert.Equal(t, "0", cards[0].ID)
	assert.Equal(t, "1", cards[1].ID)
	assert.Nil(t, FromSysfs(filepath.Join(root, "missing")))
}

func TestGPUMem_UsedFromROCmSMIAndSysfs(t *testing.T) {
	used := ParseROCmSMIUsed(`
GPU[0]		: VRAM Total Memory (B): 34342961152
GPU[0]		: VRAM Total Used Memory (B): 21474836480
GPU[1]		: VRAM Total Memory (B): 34342961152
GPU[1]		: VRAM Total Used Memory (B): 0
`)
	assert.Equal(t, map[string]float64{"0": 20, "1": 0}, used)

	root := t.TempDir()
	for card, bytes := range map[string]string{"card10": "1073741824", "card2": "3221225472"} {
		dir := filepath.Join(root, card, "device")
		require.NoError(t, os.MkdirAll(dir, 0o755))
		require.NoError(t, os.WriteFile(filepath.Join(dir, "mem_info_vram_total"), []byte("34342961152"), 0o644))
		require.NoError(t, os.WriteFile(filepath.Join(dir, "mem_info_vram_used"), []byte(bytes), 0o644))
	}
	require.NoError(t, os.MkdirAll(filepath.Join(root, "card0", "device"), 0o755)) // a display with no VRAM
	assert.Equal(t, map[string]float64{"0": 3, "1": 1}, UsedFromSysfs(root), "numbered like FromSysfs: card2 first")
}
