SELECT d.ID, `master`.getNamaLengkapPegawai(d.NIP) DOKTER 
FROM `master`.dokter d
HAVING DOKTER LIKE '%Sp.Rad%'