package state

import "testing"

func TestCancellationAndReplayState(t *testing.T) {
	s := New()
	_, replay, cancelled, done := s.Begin("key")
	if replay || cancelled {
		t.Fatal("new work should be pending")
	}
	if !s.Activate("key", done) {
		t.Fatal("active work should start")
	}
	active, wait := s.Cancel("key")
	if !active || wait != done {
		t.Fatal("active cancellation did not return completion channel")
	}
	s.Finish("key", "hash", []byte("response"), done, true)
	_, replay, cancelled, _ = s.Begin("key")
	if replay || !cancelled {
		t.Fatal("cancelled work must not replay")
	}

	_, _, _, completed := s.Begin("complete")
	s.Finish("complete", "hash", []byte("response"), completed, true)
	e, replay, _, _ := s.Begin("complete")
	if !replay || string(e.Response) != "response" {
		t.Fatal("completed work should replay")
	}
}

func TestQueuedUnknownAndRepeatedCancellationAreIdempotent(t *testing.T) {
	s := New()
	_, _, _, queuedDone := s.Begin("queued")
	active, done := s.Cancel("queued")
	if active || done != queuedDone {
		t.Fatal("queued cancellation did not expose its completion")
	}
	active, repeated := s.Cancel("queued")
	if active || repeated != queuedDone {
		t.Fatal("repeated queued cancellation changed state")
	}
	s.Finish("queued", "hash", nil, queuedDone, false)
	select {
	case <-queuedDone:
	default:
		t.Fatal("queued cancellation did not complete")
	}
	if active, done = s.Cancel("unknown"); active || done != nil {
		t.Fatal("unknown cancellation must complete immediately")
	}
	if active, done = s.Cancel("unknown"); active || done != nil {
		t.Fatal("repeated unknown cancellation must complete immediately")
	}
}
