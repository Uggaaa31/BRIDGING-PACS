-- --------------------------------------------------------
-- Host:                         10.10.160.99
-- Versi server:                 8.0.41 - MySQL Community Server - GPL
-- OS Server:                    Linux
-- HeidiSQL Versi:               12.21.0.7344
-- --------------------------------------------------------

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES utf8 */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;


-- Membuang struktur basisdata untuk ris
CREATE DATABASE IF NOT EXISTS `ris` /*!40100 DEFAULT CHARACTER SET latin1 */ /*!80016 DEFAULT ENCRYPTION='N' */;
USE `ris`;

-- membuang struktur untuk table ris.hl7_logs
CREATE TABLE IF NOT EXISTS `hl7_logs` (
  `ID` int NOT NULL AUTO_INCREMENT,
  `REF_ID` char(11) NOT NULL,
  `JENIS` tinyint NOT NULL DEFAULT '0' COMMENT '0=ORM,1=ORU',
  `PROVIDER` varchar(50) NOT NULL DEFAULT '0',
  `KODE` char(10) NOT NULL DEFAULT '',
  `TGL_REQUEST` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `REQUEST` text NOT NULL,
  `TGL_RESPONSE` datetime DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
  `RESPONSE` text NOT NULL,
  `KIRIM` tinyint NOT NULL DEFAULT '0' COMMENT '0=Belum Terkirim; 1=Terkirim',
  `STATUS` tinyint NOT NULL DEFAULT '1' COMMENT '0=Batal; 1=Aktif',
  PRIMARY KEY (`ID`),
  UNIQUE KEY `REF_ID_JENIS` (`REF_ID`,`JENIS`,`PROVIDER`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=69 DEFAULT CHARSET=latin1;

-- Pengeluaran data tidak dipilih.

/*!40103 SET TIME_ZONE=IFNULL(@OLD_TIME_ZONE, 'system') */;
/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IFNULL(@OLD_FOREIGN_KEY_CHECKS, 1) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40111 SET SQL_NOTES=IFNULL(@OLD_SQL_NOTES, 1) */;
