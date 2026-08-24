# ADR 0001: past-only temporal graph

Status: accepted.

Build graph features before inserting the current event and delay confirmed labels by 24 hours. This costs implementation complexity but prevents future edges or immediate chargeback knowledge from leaking into evaluation.
