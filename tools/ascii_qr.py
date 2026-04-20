#!/usr/bin/env python3
"""Generate a terminal-safe QR code with half-block semigraphics.

Usage: python ascii_qr.py <text> [--version V] [--error E] [--box B] [--margin M]
"""

import argparse

import qrcode

FULL_BLOCK = "█"
UPPER_HALF_BLOCK = "▀"
LOWER_HALF_BLOCK = "▄"
EMPTY = " "


def scale_matrix(matrix, box_size):
    if box_size <= 1:
        return matrix

    scaled = []
    for row in matrix:
        expanded_row = []
        for cell in row:
            expanded_row.extend([cell] * box_size)
        for _ in range(box_size):
            scaled.append(expanded_row[:])
    return scaled


def pair_to_char(top_cell, bottom_cell):
    if top_cell and bottom_cell:
        return FULL_BLOCK
    if top_cell:
        return UPPER_HALF_BLOCK
    if bottom_cell:
        return LOWER_HALF_BLOCK
    return EMPTY


def to_ascii(matrix):
    out_lines = []
    row_count = len(matrix)
    for row_index in range(0, row_count, 2):
        top_row = matrix[row_index]
        bottom_row = matrix[row_index + 1] if row_index + 1 < row_count else [False] * len(top_row)
        line = ""
        for top_cell, bottom_cell in zip(top_row, bottom_row):
            line += pair_to_char(top_cell, bottom_cell)
        out_lines.append(line)
    return '\n'.join(out_lines)


def main():
    parser = argparse.ArgumentParser(description="ASCII QR code generator")
    parser.add_argument('text', help='Text to encode in QR code')
    parser.add_argument('--version', type=int, default=0,
                        help='QR version (1-40) or 0 for auto')
    parser.add_argument('--error', choices=['L', 'M', 'Q', 'H'], default='M',
                        help='Error correction level')
    parser.add_argument('--box', type=int, default=1,
                        help='Box size')
    parser.add_argument('--margin', type=int, default=4,
                        help='Quiet zone margin')
    args = parser.parse_args()

    qr = qrcode.QRCode(
        version=args.version if args.version != 0 else None,
        error_correction=getattr(qrcode.constants,
                                 f'ERROR_CORRECT_{args.error}'),
        box_size=args.box,
        border=args.margin,
    )
    qr.add_data(args.text)
    qr.make(fit=True)
    matrix = scale_matrix(qr.get_matrix(), args.box)
    ascii_art = to_ascii(matrix)
    print(ascii_art)


if __name__ == '__main__':
    main()
