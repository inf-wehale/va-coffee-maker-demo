/*****************************************************************************
* \file Coffee_ifx_va_config_prms.c
*****************************************************************************
* \copyright
* Copyright 2025, Infineon Technologies.
* All rights reserved.
*****************************************************************************/

#include "Coffee_ifx_va_config_prms.h"

int32_t Coffee_sod_prms[] = {
	0,
	16000,
	160,
	IFX_PRE_PROCESS_IP_COMPONENT_SOD,
	2,
	400,
	16384
};

int32_t Coffee_pre_proc_hpf_prms[] = {
	0,
	16000,
	160,
	IFX_PRE_PROCESS_IP_COMPONENT_HPF,
	0
};

int32_t Coffee_denoise_prms[] = {
	0,
	16000,
	160,
	IFX_PRE_PROCESS_IP_COMPONENT_DENOISE,
	1,
	38
};

int32_t Coffee_dfww_prms[] = {
	0,
	16000,
	160,
	IFX_POST_PROCESS_IP_COMPONENT_DFWWD,
	1,
	500
};

int32_t Coffee_dfcmd_prms[] = {
	0,
	16000,
	160,
	IFX_POST_PROCESS_IP_COMPONENT_DFCMD,
	2,
	1500,
	0
};
