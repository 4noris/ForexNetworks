package main

// Large position replacements make an event-count limit alone insufficient.
// This history serves only the legacy raw stream; state replication uses its
// independent per-entity versions and deletion floor.
const maxEventHistoryBytes = 16 * 1024 * 1024

func eventBytes(e Event) int {
	return len(e.Data) + len(e.ID) + len(e.Kind) + len(e.Op) + len(e.Epoch) + 128
}

func (b *Book) retainEvent(e Event) {
	b.ring = append(b.ring, e)
	b.ringBytes += eventBytes(e)
	for len(b.ring) > 0 && (b.ringBytes > maxEventHistoryBytes || len(b.ring) > 32768) {
		b.ringBytes -= eventBytes(b.ring[0])
		// Clear backing-array references so dropped snapshots can be collected.
		b.ring[0] = Event{}
		b.ring = b.ring[1:]
	}
}
