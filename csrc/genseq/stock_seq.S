    .text
    .global gseq_seq_new
gseq_seq_new:
    move.l  4(%sp),%a0
    jmp     0x4019bc5e

    .global gseq_seq_get
gseq_seq_get:
    move.l  %a2,-(%sp)
    move.l  8(%sp),%a2
    jsr     0x4018d6b2
    clr.l   -(%sp)
    move.l  %d0,-(%sp)
    move.l  %a2,%a0
    jsr     0x4019b850
    addq.l  #8,%sp
    move.l  (%sp)+,%a2
    rts

    .section .note.GNU-stack,"",@progbits
